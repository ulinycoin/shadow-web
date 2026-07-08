# Attack Surface Security Scan

**Generated:** 2026-07-08T03:57:27.704719+00:00
**Scope:** automated surface mapping (not penetration testing)

## Executive summary

- Pages scanned: **2**
- Pages failed: **0**
- Findings: critical **0**, high **0**, medium **5**, low **0**, info **0**

### Highest-risk pages

- `https://localpdf.online/` — score 15
- `https://localpdf.online/pricing` — score 10

## Findings by page

### Private PDF Editor — Edit, Merge & OCR in Browser | LocalPDF

- URL: `https://localpdf.online/`
- page_class: **Static** — Standard HTML page with static layout.
- action_count: 86, forms: 0

- **[MEDIUM]** Link to potentially sensitive path (`LINK_SENSITIVE_PATH`)
  Surface exposes https://localpdf.online/use-cases/internal-operations
- **[MEDIUM]** Link to potentially sensitive path (`LINK_SENSITIVE_PATH`)
  Surface exposes https://localpdf.online/features/split-pdf
- **[MEDIUM]** Link to potentially sensitive path (`LINK_SENSITIVE_PATH`)
  Surface exposes https://localpdf.online/use-cases/internal-operations

### LocalPDF Pricing — Free & Pro Plans from $3.99/mo | Private PDF Tool

- URL: `https://localpdf.online/pricing`
- page_class: **Static** — Standard HTML page with static layout.
- action_count: 56, forms: 0

- **[MEDIUM]** Link to potentially sensitive path (`LINK_SENSITIVE_PATH`)
  Surface exposes https://localpdf.online/use-cases/internal-operations
- **[MEDIUM]** Link to potentially sensitive path (`LINK_SENSITIVE_PATH`)
  Surface exposes https://localpdf.online/use-cases/internal-operations

## Limitations

- Does not test XSS, SQLi, auth bypass, or HTTP security headers.
- Use only on systems you are authorized to scan.
