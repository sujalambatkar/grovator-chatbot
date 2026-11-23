import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")  # estimate; Llama's tokenizer differs slightly


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def truncate_tokens(text: str, max_tokens: int) -> str:
    ids = _enc.encode(text)
    return text if len(ids) <= max_tokens else _enc.decode(ids[:max_tokens]).rstrip() + "…"
