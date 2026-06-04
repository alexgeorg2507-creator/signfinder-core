# TECH_DEBT.md — SignFinder Core

| ID | Описание | Версия | Приоритет |
|----|----------|--------|-----------|
| TD-01 | `sf.sign()` не принимает signer_id явно | v1.9 | Medium |
| TD-02 | `sf.render_page()` не на фасаде | v1.9 | Low |
| TD-03 | Template CRUD не полностью на фасаде | v1.9 | Medium |
| TD-04 | `fingerprint_config.json` — конфигурация матчера не вынесена в JSON | v1.9 | Medium |
| TD-05 | `add_anchors_to_template` не через API endpoint | v1.9 | Low |
| TD-06 | `llm_config.json` хранит ключи в открытом виде — шифрование при выходе в облако | **v1.10** | **HIGH** — блокер для облачного деплоя |
| ~~TD-07~~ | ~~Структурный паттерн `_{3,}\s*\([^)]{3,40}\)` был захардкожен в pipeline-коде~~ | ~~v1.17.7: вынесен в markers-конфиг, удалён `_signer_initials_pattern`~~ | ~~ЗАКРЫТ~~ |
| TD-08 | Dedup якорей в Streamlit — оверматч структурного паттерна (обе стороны) | v1.17.7 | Medium |
