# Named edge cases

Each case has demo PDFs and form values in `edge_cases/`, and an end-to-end test in
`tests/test_edge_cases.py` that runs the real PDFs through extraction, the engine, the review
workflow and the decision. Run `python -m pytest tests/test_edge_cases.py -v`.

## EC1 — An approved vendor changes its bank account

**Files:** `edge_cases/EC1_bank_account_change/` — `step1_original_onboarding/` (3 PDFs) and
`step2_change_request/03_Bank_Proof_NEW.pdf`. Forms in `forms.json`: `step1_original`,
`step2a_bank_change_only`, `step2b_bank_and_contact_change`.

**Demo:** onboard with step1 and approve it. Submit again with the same PAN/GST PDFs, the NEW bank
proof and form `step2a` (or `step2b`).

**Expected:**
* 2a → `BANK_DETAILS_CHANGED` (High). 2b → also `BANK_AND_CONTACT_CHANGED` (High), because the
  email and phone changed in the same submission (business-email-compromise pattern).
* Recommendation **Pending: needs human review**. No message to the vendor; an **internal callback
  task** says to call the contact details in the approved master, not the new ones.
* The approved vendor master keeps the old account until approval.
* Resolution needs a 30+ character note. The reviewer who resolved it **cannot approve**; a second
  reviewer approves, and only then does the master switch to the new account.

## EC2 — Proprietorship: account held by the owner, not the trade name

**Files:** `edge_cases/EC2_proprietorship_owner_account/` + `form.json`. PAN `BXRPK4821L`
(4th character **P** = individual/proprietor). GST legal name RAMESH KUMAR, trade name KUMAR TRADERS.
The form uses "Kumar Traders"; the bank account holder is RAMESH KUMAR.

**Expected:**
* `PROPRIETOR_TRADE_NAME_USED` (Low, info only) instead of a High legal-name mismatch.
* `ACCOUNT_HOLDER_PROPRIETOR_OWNER` (Medium, Needs Manual Review), explained as expected for this
  entity type, instead of the generic High `FORM_DOCUMENT_MISMATCH_ACCOUNT_HOLDER`.
* Recommendation **Pending: needs human review**; after one resolution it is approvable.
* For a company (PAN 4th character C), the same account-holder difference stays a High mismatch.

## EC3 — Same bank account used by two different PANs

**Files:** `edge_cases/EC3_shared_bank_account/vendor_A_existing/` (ABC Infrastructure) and
`vendor_B_new/` (XYZ Consultants, different PAN, bank proof showing account 123456789012).

**Demo:** onboard vendor A, then vendor B.

**Expected:** `BANK_ACCOUNT_SHARED_WITH_OTHER_PAN` (High, unresolvable) at onboarding →
**Recommend reject**. Approval is blocked and the finding cannot be resolved. The vendor message is
a neutral hold message that does not reveal the fraud signal; the internal task says to escalate.

## EC4 — GSTIN state differs from the address (branch registration)

**Files:** `edge_cases/EC4_gstin_state_vs_address/` + `form.json`. GSTIN state code 27
(Maharashtra); the GST certificate address is in Pune; the form's head-office address is in
Bengaluru, Karnataka.

**Expected:** `GSTIN_STATE_ADDRESS_MISMATCH` (Medium, Needs Manual Review) → **Pending: needs human
review**, not reject. The vendor message asks whether this is a branch / additional place of
business registration.

## Bulk version

`samples/bulk/` runs the clean case, EC2, EC4 and EC3 in one batch (in that order, so EC3 sees the
account registered by CASE001). Expected: Approve-ready, Pending review, Pending review, Recommend
reject. EC1 is not in the bulk sample because it needs a human approval between the two submissions.
