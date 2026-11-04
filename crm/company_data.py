"""Present source address fields without inferring geography or main activity."""
def address_text(record):
    def scalar(key):
        value = record.get(key)
        return str(value).strip() if isinstance(value,(str,int)) else ''
    parts = [scalar(key) for key in ['nindex','vregion','vdistrict','vnp','vulitsa']]
    for key,label in [('vdom','д.'),('vkorp','корп.'),('vpom','пом.')]:
        if scalar(key): parts.append(f'{label} {scalar(key)}')
    parts.append(scalar('vadrprim'))
    return ', '.join(part for part in parts if part)
