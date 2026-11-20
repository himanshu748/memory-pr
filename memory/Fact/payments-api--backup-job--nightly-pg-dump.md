---
type: Fact
name: payments-api--backup-job--nightly-pg-dump
predicate: backup-job
status: active
value: nightly-pg-dump
ABOUT:
  - "[[Entity/payments-api]]"
ASSERTED_BY:
  - "[[Agent/scout]]"
DERIVED_FROM:
  - "[[Fact/payments-api--database--postgres-15]]"
SOURCE:
  - "[[Source/demo-project-services-payments-api-service-yaml]]"
---

payments-api backup-job: nightly-pg-dump
