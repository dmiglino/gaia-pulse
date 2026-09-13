---
name: security-review
description: Use before completing any change touching auth, sessions, personal health data, secrets, or file uploads (voice/blood-analysis).
---

# Run a security/privacy review

1. Trace the concrete exploit: could one household member's session or
   request reach the other member's body metrics, blood analysis, or
   preferences?
2. Check session/token handling: `itsdangerous` signing, `APP_SECRET_KEY`
   sourced from env (not the dev default) in production.
3. Check every new query against personal tables filters by `user_id`.
4. Check secrets never appear in logs, error responses, or
   `.env.example`.
5. For file uploads (STT audio, blood-analysis documents): validate
   type/size before parsing; treat contents as untrusted.
6. Report with a verdict: `APPROVE`, `APPROVE WITH FOLLOW-UP`, or `BLOCK`.
