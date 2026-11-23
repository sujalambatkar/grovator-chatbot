Knowledge sources. Files here are loaded by `app/rag/ingest.py` and `app/content/catalog.py`.

* `hardware/grovator-esp32-gateway.json` is the **real Grovator ESP32 IoT Gateway dataset** (interfaces, devices, wiring rules,
  concepts). `app/rag/hardware.py` turns every record into documents at load time, so editing or extending the JSON changes what
  the chatbot knows; nothing is written per sensor. Add a device to `devices` and its connection guide appears automatically.
* Markdown files (`*.md` with front-matter `title`, `category`, `order`, `role_visibility`) are still supported for extra pages.

The old dummy courses/lessons/blocks now live only as a TEST fixture: `backend/tests/fixtures/knowledge_synthetic/`.
