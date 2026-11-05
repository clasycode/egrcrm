import base64
import hashlib
import hmac
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from deploy.key_to_openssh import convert,integer,length,ssh_values
from deploy.make_release import package,ROOT_DIRS,ROOT_FILES


def string(value):
    if isinstance(value,str):value=value.encode()
    return struct.pack('>I',len(value))+value


def fixture_ppk():
    # Deliberately tiny, public textbook RSA numbers; never used for SSH.
    public=string('ssh-rsa')+string((17).to_bytes(1,'big'))+string((3233).to_bytes(2,'big'))
    private=b''.join(string(n.to_bytes((n.bit_length()+7)//8,'big')) for n in (2753,61,53,38))
    data=b''.join(string(v) for v in ('ssh-rsa','none','unit-test',public,private))
    mac=hmac.new(hashlib.sha1(b'putty-private-key-file-mac-key').digest(),data,hashlib.sha1).hexdigest()
    return ('PuTTY-User-Key-File-2: ssh-rsa\nEncryption: none\nComment: unit-test\nPublic-Lines: 1\n'+base64.b64encode(public).decode()+'\nPrivate-Lines: 1\n'+base64.b64encode(private).decode()+'\nPrivate-MAC: '+mac+'\n').encode()


class KeyConversionTests(unittest.TestCase):
    def test_valid_integrity_and_der_encoding(self):
        result=convert(fixture_ppk())
        self.assertTrue(result.startswith(b'-----BEGIN RSA PRIVATE KEY-----\n'))
        der=base64.b64decode(b''.join(result.splitlines()[1:-1]))
        body=b''.join(integer(n) for n in (0,3233,17,2753,61,53,53,49,38))
        self.assertEqual(der,b'\x30'+length(len(body))+body)

    def test_rejects_corruption_and_encryption(self):
        with self.assertRaisesRegex(ValueError,'integrity'):
            convert(fixture_ppk().rsplit(b'Private-MAC:',1)[0]+b'Private-MAC: '+b'0'*40+b'\n')
        with self.assertRaisesRegex(ValueError,'unencrypted'):
            convert(fixture_ppk().replace(b'Encryption: none',b'Encryption: aes256-cbc'))
        with self.assertRaises(ValueError):ssh_values(b'\x00\x00\x10\x00x')


class ReleasePackagingTests(unittest.TestCase):
    def fixture(self,root):
        for name in ROOT_FILES:(root/name).write_text('source')
        for name in ROOT_DIRS:
            (root/name).mkdir();(root/name/'source.py').write_text('source')

    def test_credentials_databases_and_caches_never_enter_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'project';root.mkdir();self.fixture(root)
            for name in ('.env','.env.production','key.ppk','private.pem','secret.key','dev.sqlite3','data.db'):
                (root/'crm'/name).write_text('not-for-release')
            (root/'crm'/'.venv').mkdir();(root/'crm'/'.venv'/'source.py').write_text('not-for-release')
            (root/'.local-key').write_text('not-for-release')
            target=base/'release.tar.gz';digest=package(root,target)
            self.assertEqual(digest,hashlib.sha256(target.read_bytes()).hexdigest())
            with tarfile.open(target) as archive:
                for member in archive:
                    self.assertNotIn(b'not-for-release',archive.extractfile(member).read())

    def test_source_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'project';root.mkdir();self.fixture(root)
            (base/'outside').write_text('not-for-release')
            (root/'crm'/'linked.py').symlink_to(base/'outside')
            with self.assertRaisesRegex(ValueError,'symlinks'):package(root,base/'release.tar.gz')


if __name__=='__main__':unittest.main()
