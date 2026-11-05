import re
import phonenumbers

def normalized_contact(kind,raw):
    if kind=='phone':
        try:
            phone=phonenumbers.parse(raw,'BY')
            if phonenumbers.is_valid_number(phone): return phonenumbers.format_number(phone,phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException: pass
        return re.sub(r'\D','',raw)
    return raw.strip().casefold()

def contact_parts(kind,raw):
    if kind!='phone': return [raw]
    parts=[part.strip() for part in re.split(r'[;,\n]+',raw) if part.strip()]
    if len(parts)>1 and all(normalized_contact('phone',part).startswith('+') for part in parts): return parts
    return [raw]

def search_contact(raw):
    if re.fullmatch(r'[+()0-9\s.\-/]+',raw) and any(c.isdigit() for c in raw):
        return re.sub(r'\D','',raw)
    return raw.casefold()


def messenger_phone(raw):
    """Only expose a single, valid E.164 number to messenger deep links."""
    if not re.fullmatch(r'[+0-9()\s.\-]+',raw.strip()): return ''
    try:
        phone=phonenumbers.parse(raw,'BY')
        if phonenumbers.is_valid_number(phone) and not phone.extension:
            return phonenumbers.format_number(phone,phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException: pass
    return ''
