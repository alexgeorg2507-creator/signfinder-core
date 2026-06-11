# TECH_DEBT.md — SignFinder Core

| ID | Описание | Версия | Приоритет |
|----|----------|--------|-----------|
| TD-01 | `sf.sign()` не принимает signer_id явно | v1.9 | Medium |
| TD-02 | `sf.render_page()` не на фасаде | v1.9 | Low |
| TD-03 | Template CRUD не полностью на фасаде | v1.9 | Medium |
| TD-04 | `fingerprint_config.json` — конфигурация матчера не вынесена в JSON | v1.9 | Medium |
| TD-05 | `add_anchors_to_template` не через API endpoint | v1.9 | Low |
| TD-06 | **СЕКРЕТЫ В ОТКРЫТОМ ВИДЕ.** `llm_config.json` (LLM-ключи) И `mail_config.json` (IMAP/SMTP пароли + OAuth2 client_secret/refresh_token, v1.18.0) хранятся НЕЗАШИФРОВАННЫМИ на диске. **ШИФРОВАНИЕ ОБЯЗАТЕЛЬНО ПРИ ВЫХОДЕ В ОБЛАКО.** Пока шлифуем локальную версию под старт клиента — терпимо (single-user, локальный Docker, доступ только у владельца). НЕ выкатывать в multi-tenant / публичный хостинг без шифрования секретов (KMS / vault / шифрованный том). | **v1.10 / v1.18.0** | **HIGH — БЛОКЕР ОБЛАЧНОГО ДЕПЛОЯ** |
| ~~TD-07~~ | ~~Структурный паттерн `_{3,}\s*\([^)]{3,40}\)` был захардкожен в pipeline-коде~~ | ~~v1.17.7: вынесен в markers-конфиг, удалён `_signer_initials_pattern`~~ | ~~ЗАКРЫТ~~ |
| TD-08 | Dedup якорей в Streamlit — оверматч структурного паттерна (обе стороны) | v1.17.7 | Medium |
| TD-09 | OAuth2 refresh_token consent — одноразовый ручной шаг (получается вне приложения через get_refresh_token.py). Для single-user ок; для multi-tenant нужен полноценный OAuth-flow в UI (authorize redirect → callback → обмен кода) | v1.18.0 | Low (пока single-user) |
