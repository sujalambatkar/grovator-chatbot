import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, production_problems
from app.main import create_app
from tests.conftest import FakeLLM, make_token

GOOD_PROD = dict(environment="production", jwt_secret="x" * 40, groq_api_key="gsk_test", platform_adapter="none",
                 redis_url="redis://r:6379", qdrant_url="http://q:6333", cors_origins="https://app.govator.example")


def test_development_has_no_startup_checks():
    assert production_problems(Settings(environment="development")) == []


def test_a_correct_production_config_has_no_problems():
    assert production_problems(Settings(**GOOD_PROD)) == []


@pytest.mark.parametrize("override,needle", [
    ({"jwt_secret": "dev-secret-change-me"}, "JWT_SECRET"),
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"groq_api_key": ""}, "GROQ_API_KEY"),
    ({"platform_adapter": "mock"}, "FAKE student data"),
    ({"redis_url": "memory"}, "REDIS_URL"),
    ({"qdrant_url": "memory"}, "QDRANT_URL"),
    ({"cors_origins": "http://localhost:3000"}, "CORS_ORIGINS"),
    ({"cors_origins": "*"}, "CORS_ORIGINS"),
])
def test_each_unsafe_production_setting_is_reported(override, needle):
    problems = production_problems(Settings(**{**GOOD_PROD, **override}))
    assert len(problems) == 1 and needle in problems[0]


def test_app_refuses_to_start_in_production_with_defaults(retriever):
    with pytest.raises(RuntimeError, match="Refusing to start in production"):
        create_app(llm=FakeLLM(), retriever=retriever, settings=Settings(environment="production"))


def test_tokens_without_exp_or_sub_are_rejected(retriever):
    s = Settings(usage_log_dir="")
    secret, alg = s.jwt_secret, s.jwt_algorithm
    no_exp = jwt.encode({"sub": "u", "role": "student", "org_id": "org1"}, secret, algorithm=alg)
    no_sub = jwt.encode({"role": "student", "org_id": "org1", "exp": int(time.time()) + 600}, secret, algorithm=alg)
    expired = jwt.encode({"sub": "u", "role": "student", "org_id": "org1", "exp": int(time.time()) - 5}, secret, algorithm=alg)
    wrong_alg = jwt.encode({"sub": "u", "role": "student", "org_id": "org1", "exp": int(time.time()) + 600}, secret, algorithm="HS512")
    none_alg = jwt.encode({"sub": "u", "role": "student", "org_id": "org1", "exp": int(time.time()) + 600}, None, algorithm="none")
    with TestClient(create_app(llm=FakeLLM(), retriever=retriever, settings=s)) as c:
        for tok in (no_exp, no_sub, expired, wrong_alg, none_alg):
            r = c.post("/chat", json={"message": "How do I connect the IoT kit?"}, headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == 401, tok
        ok = c.post("/chat", json={"message": "How do I connect the IoT kit?"}, headers={"Authorization": f"Bearer {make_token('student')}"})
        assert ok.status_code == 200
