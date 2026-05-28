# TECH_DEBT.md — SignFinder Core

| ID | Описание | Версия | Приоритет |
|----|----------|--------|-----------|
| TD-01 | `sf.sign()` не принимает signer_id явно | v1.9 | Medium |
| TD-02 | `sf.render_page()` не на фасаде | v1.9 | Low |
| TD-03 | Template CRUD не полностью на фасаде | v1.9 | Medium |
| TD-04 | `fingerprint_config.json` — конфигурация матчера не вынесена в JSON | v1.9 | Medium |
| TD-05 | `add_anchors_to_template` не через API endpoint | v1.9 | Low |
| TD-06 | `llm_config.json` хранит ключи в открытом виде — шифрование при выходе в облако | **v1.10** | **HIGH** — блокер для облачного деплоя |
