"""Convert an unencrypted RSA PuTTY v2 key locally; never print key material."""
import argparse
import base64
import hashlib
import hmac
from pathlib import Path
import struct


def ssh_values(blob):
    result=[]
    while blob:
        if len(blob)<4:raise ValueError('Invalid key structure')
        size=struct.unpack('>I',blob[:4])[0]
        if size>len(blob)-4:raise ValueError('Invalid key structure')
        result.append(blob[4:4+size]);blob=blob[4+size:]
    return result


def length(size):
    if size<128:return bytes([size])
    encoded=size.to_bytes((size.bit_length()+7)//8,'big')
    return bytes([128+len(encoded)])+encoded


def integer(value):
    data=value.to_bytes(max(1,(value.bit_length()+7)//8),'big')
    if data[0]&128:data=b'\0'+data
    return b'\x02'+length(len(data))+data


def convert(raw):
    if not raw.startswith(b'PuTTY-User-Key-File-'):
        if raw.startswith((b'-----BEGIN OPENSSH PRIVATE KEY-----',b'-----BEGIN RSA PRIVATE KEY-----',b'-----BEGIN PRIVATE KEY-----')):return raw
        raise ValueError('Expected a PuTTY PPK or OpenSSH private key')
    lines=raw.decode('utf-8-sig').splitlines();fields={};index=0
    while index<len(lines):
        key,separator,value=lines[index].partition(': ');index+=1
        if not separator:raise ValueError('Invalid PPK structure')
        if key in ('Public-Lines','Private-Lines'):
            count=int(value)
            if count<1 or count>10000:raise ValueError('Invalid PPK length')
            fields[key]=base64.b64decode(''.join(lines[index:index+count]),validate=True);index+=count
        else:fields[key]=value
    if fields.get('PuTTY-User-Key-File-2')!='ssh-rsa' or fields.get('Encryption')!='none':
        raise ValueError('This installer supports unencrypted RSA PPK v2; encrypted keys must first be exported with PuTTYgen')
    def string(value):
        value=value.encode() if isinstance(value,str) else value
        return struct.pack('>I',len(value))+value
    mac_data=b''.join(string(fields[key]) for key in ('PuTTY-User-Key-File-2','Encryption','Comment','Public-Lines','Private-Lines'))
    expected=hmac.new(hashlib.sha1(b'putty-private-key-file-mac-key').digest(),mac_data,hashlib.sha1).hexdigest()
    if not hmac.compare_digest(expected,fields['Private-MAC']):raise ValueError('PPK integrity check failed')
    public=ssh_values(fields['Public-Lines']);private=ssh_values(fields['Private-Lines'])
    if len(public)!=3 or public[0]!=b'ssh-rsa' or len(private)!=4:raise ValueError('Invalid RSA key')
    e,n=[int.from_bytes(v,'big') for v in public[1:]]
    d,p,q,iqmp=[int.from_bytes(v,'big') for v in private]
    if min(e,d,p,q)<2 or p*q!=n or (iqmp*q)%p!=1:raise ValueError('RSA consistency check failed')
    body=b''.join(integer(v) for v in (0,n,e,d,p,q,d%(p-1),d%(q-1),iqmp))
    der=b'\x30'+length(len(body))+body
    encoded=base64.b64encode(der)
    return b'-----BEGIN RSA PRIVATE KEY-----\n'+b'\n'.join(encoded[i:i+64] for i in range(0,len(encoded),64))+b'\n-----END RSA PRIVATE KEY-----\n'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('target',type=Path)
    args=parser.parse_args()
    try:
        output=convert(args.source.read_bytes())
        with args.target.open('xb') as stream:stream.write(output)
        args.target.chmod(0o600)
    except (ValueError,KeyError,UnicodeError,OSError) as error:raise SystemExit('Key conversion failed: '+str(error))
    print('SSH key prepared locally. Key contents were not displayed.')


if __name__=='__main__':main()
