"""Turn findings into (a) one plain-language message for the vendor and
(b) internal tasks for the review team.

Rules:
- The vendor never sees rule IDs, risk scores or fraud signals.
- Bank-change checks are internal callbacks, never a request to re-upload.
- Identifiers in messages are masked (last 4 characters only).
"""
from core.engine import is_resolved
from core.privacy import mask

DOC_PAN = "a clear, text-searchable PDF of the PAN card (or PAN allotment letter) of the legal entity"
DOC_GST = "the current GST registration certificate (text-searchable PDF)"
DOC_BANK = ("a cancelled cheque or a recent bank statement (text-searchable PDF) showing the "
            "account holder name, account number and IFSC")

VENDOR_TEXT = {
    "DOC_REQUIRED": "Please upload all three required documents: the PAN card, the GST registration certificate and a cancelled cheque or bank statement.",
    "DOC_SCAN_REVIEW": "We could not read the text in {observed}. Please upload a text-searchable PDF (not a photo or scan) of the same document.",
    "DOC_TYPE_CONTENT_MISMATCH": "The file uploaded as the {expected} ({observed}) does not appear to be a {expected}. Please upload the correct document.",
    "DUPLICATE_DOCUMENT_IN_SUBMISSION": "The same file was uploaded in more than one document slot. Please upload a separate, correct file for each document.",
    "FORM_REQUIRED_PAN": "Please enter the PAN of the legal entity in the form.",
    "FORM_REQUIRED_GSTIN": "Please enter the GSTIN in the form.",
    "FORM_REQUIRED_IFSC": "Please enter the IFSC of the bank branch in the form.",
    "FORM_REQUIRED_BANK_ACCOUNT": "Please enter the bank account number in the form.",
    "INVALID_FORMAT_PAN": "The PAN entered ({m_observed}) is not in the correct format (5 letters, 4 digits, 1 letter). Please check and correct it.",
    "INVALID_PAN_HOLDER_TYPE": "The PAN entered ({m_observed}) does not look valid. Please check it for typing errors.",
    "INVALID_FORMAT_GSTIN": "The GSTIN entered ({m_observed}) is not in the correct format. Please check and correct it.",
    "INVALID_CHECKSUM_GSTIN": "The GSTIN entered ({m_observed}) does not pass the GSTIN check-digit test, which usually means a typing error. Please check and correct it.",
    "INVALID_FORMAT_IFSC": "The IFSC entered ({observed}) is not in the correct format (for example HDFC0001234). Please check and correct it.",
    "INVALID_FORMAT_BANK_ACCOUNT": "The bank account number entered must contain 9 to 18 digits. Please check and correct it.",
    "FORM_DOCUMENT_MISMATCH_PAN": "The PAN in the form (ending {m4_expected}) does not match the PAN on your documents (ending {m4_observed}). Please correct the form or upload the correct PAN document.",
    "FORM_DOCUMENT_MISMATCH_GSTIN": "The GSTIN in the form (ending {m4_expected}) does not match the GST certificate (ending {m4_observed}). Please correct the form or upload the correct certificate.",
    "FORM_DOCUMENT_MISMATCH_IFSC": "The IFSC in the form ({expected}) does not match the bank document ({observed}). Please correct the form or upload matching bank proof.",
    "FORM_DOCUMENT_MISMATCH_BANK_ACCOUNT": "The bank account number in the form (ending {m4_expected}) does not match the bank document (ending {m4_observed}). Please correct the form or upload bank proof for the account you entered.",
    "FIELD_NOT_FOUND_PAN": "We could not find the PAN in your documents. Please upload " + DOC_PAN + ".",
    "FIELD_NOT_FOUND_GSTIN": "We could not find the GSTIN in your documents. Please upload " + DOC_GST + ".",
    "FIELD_NOT_FOUND_IFSC": "We could not find the IFSC on your bank document. Please upload " + DOC_BANK + ".",
    "FIELD_NOT_FOUND_BANK_ACCOUNT": "We could not find the bank account number on your bank document. Please upload " + DOC_BANK + ".",
    "REQUIRED_FIELD_MISSING_LEGAL_NAME": "We could not read the legal name on the GST certificate. Please upload " + DOC_GST + ".",
    "REQUIRED_FIELD_MISSING_ACCOUNT_HOLDER": "We could not read the account holder name on the bank document. Please upload " + DOC_BANK + ".",
    "FORM_DOCUMENT_MISMATCH_LEGAL_NAME": "The legal name in the form (\"{expected}\") differs from the GST certificate (\"{observed}\"). Please confirm the correct legal name and correct the form if needed.",
    "CROSS_DOCUMENT_NAME_MISMATCH_PAN_GST": "The name on the PAN document (\"{observed}\") differs from the legal name on the GST certificate (\"{expected}\"). Please confirm which is correct or upload consistent documents.",
    "FORM_DOCUMENT_MISMATCH_ACCOUNT_HOLDER": "The bank account is held in the name \"{observed}\", which does not match your registered business name. Payments can only be made to an account held by the vendor. Please provide bank proof for an account in the vendor's name.",
    "GSTIN_STATE_ADDRESS_MISMATCH": "Your GSTIN is registered in a different state from the address you entered. If this is a branch or additional place of business, please confirm this and, if available, share the registration certificate for that place of business.",
    "CROSS_DOCUMENT_CONFLICT_GSTIN": "Your GST certificate shows more than one GSTIN. Please upload the certificate for the GSTIN you are registering with.",
    "CROSS_DOCUMENT_CONFLICT_IFSC": "Your bank document shows more than one IFSC. Please upload proof for a single account.",
    "CROSS_DOCUMENT_CONFLICT_BANK_ACCOUNT": "Your bank document shows more than one account number. Please upload proof for the single account you want payments made to.",
}

# Findings that must never be sent to the vendor.
INTERNAL_TEXT = {
    "BANK_DETAILS_CHANGED": "Call back the vendor on the phone number already on file in the approved vendor master (not the one in this submission) and confirm the new bank account ending {m4_observed}. Record who you spoke to. A second reviewer must approve.",
    "BANK_AND_CONTACT_CHANGED": "Bank account and {observed} changed together (business-email-compromise pattern). Do NOT use the new contact details for the callback; use only the previously approved contact details.",
    "BANK_ACCOUNT_SHARED_WITH_OTHER_PAN": "Bank account ending {m4_observed} is already registered to a vendor with a different PAN. Escalate to fraud / compliance. Do not tell the vendor which vendor shares the account.",
    "DOCUMENT_REUSED_ACROSS_VENDORS": "An identical document file was submitted by another vendor with a different PAN. Escalate to fraud / compliance.",
    "GSTIN_PAN_LINK": "The PAN in the GSTIN does not match the form PAN. Recommend rejection.",
    "CROSS_DOCUMENT_CONFLICT_PAN": "Different PANs appear across the submitted documents. Recommend rejection and investigate the documents.",
    "ACCOUNT_HOLDER_PROPRIETOR_OWNER": "Account held in the proprietor's own name (\"{observed}\"). Expected for a proprietorship: confirm the name matches the PAN holder and resolve.",
    "ACCOUNT_HOLDER_TRADE_NAME": "Account held in the trade name (\"{observed}\"). Confirm it belongs to the legal entity and resolve.",
}

REJECT_HOLD_MESSAGE = ("Thank you for your submission. We are unable to proceed with your registration "
                       "based on the information provided. Our vendor management team will contact you "
                       "if anything further is needed.")


def _format(template, finding):
    observed = str(finding.get("observed_value") or "")
    expected = str(finding.get("expected_value") or "")
    return template.format(
        observed=observed, expected=expected, source=finding.get("source_document") or "",
        m_observed=mask(observed), m_expected=mask(expected),
        m4_observed=observed[-4:], m4_expected=expected[-4:],
    )


def build_requests(findings):
    """Return one item per open finding: {audience, field, rule_id, reason, request}.

    audience is 'vendor' or 'internal'. Low/Info findings produce nothing.
    """
    items, seen = [], set()
    for finding in findings:
        if is_resolved(finding) or finding.get("severity") == "Low":
            continue
        rule = finding.get("rule_id", "")
        if rule in INTERNAL_TEXT:
            audience, text = "internal", _format(INTERNAL_TEXT[rule], finding)
        elif rule in VENDOR_TEXT:
            audience, text = "vendor", _format(VENDOR_TEXT[rule], finding)
        else:
            # Unknown rule: never expose the code to the vendor; route it internally.
            audience = "internal"
            text = f"Review finding on {finding.get('field_name', 'submission')}: {finding.get('explanation', '')}"
        if (audience, text) in seen:
            continue
        seen.add((audience, text))
        items.append({"audience": audience, "field": finding.get("field_name"), "rule_id": rule,
                      "reason": finding.get("explanation", ""), "request": text})
    return items


def build_vendor_message(vendor_name, findings, recommendation=None):
    """One consolidated, readable message for the vendor (or None if nothing is needed)."""
    name = vendor_name or "Vendor"
    if recommendation and recommendation.get("outcome") == "Rejected":
        return f"Dear {name},\n\n{REJECT_HOLD_MESSAGE}\n\nRegards,\nVendor Management Team"
    items = [i for i in build_requests(findings) if i["audience"] == "vendor"]
    if not items:
        return None
    lines = [f"Dear {name},", "",
             "Thank you for your vendor registration. Before we can continue, we need the following from you:", ""]
    lines += [f"{n}. {item['request']}" for n, item in enumerate(items, start=1)]
    lines += ["", "Please reply with the corrected details or upload the documents through the vendor portal. "
              "If you have questions, contact the vendor management team using the contact details you already have for us.",
              "", "Regards,", "Vendor Management Team"]
    return "\n".join(lines)


def build_internal_tasks(findings):
    return [i["request"] for i in build_requests(findings) if i["audience"] == "internal"]
