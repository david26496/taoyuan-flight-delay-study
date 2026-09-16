"""Exercise real git pushes against a disposable local bare repository."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/persist_snapshots.sh'


class GitPersistenceTests(unittest.TestCase):
    def test_push_no_changes_rebase_and_immutable_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);remote=base/'remote.git';checkout=base/'checkout';other=base/'other'
            env={**os.environ,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,
                 'GIT_AUTHOR_NAME':'Test','GIT_AUTHOR_EMAIL':'test@example.invalid',
                 'GIT_COMMITTER_NAME':'Test','GIT_COMMITTER_EMAIL':'test@example.invalid',
                 'TARGET_BRANCH':'main'}
            def git(*args,cwd=base):
                return subprocess.run(['git',*args],cwd=cwd,env=env,check=True,capture_output=True,text=True).stdout.strip()
            def persist():
                return subprocess.run(['bash',str(SCRIPT),'taoyuan'],cwd=checkout,env=env,capture_output=True,text=True)
            git('init','--bare',str(remote));git('init','-b','main',str(checkout))
            git('remote','add','origin',str(remote),cwd=checkout)
            (checkout/'README.md').write_text('test\n');git('add','README.md',cwd=checkout);git('commit','-m','init',cwd=checkout)
            git('push','-u','origin','main',cwd=checkout)
            git('clone','--branch','main',str(remote),str(other))
            raw=checkout/'raw/taoyuan/a_flight_20260917_000700.txt';raw.parent.mkdir(parents=True);raw.write_bytes(b'original\r\n')
            self.assertEqual(persist().returncode,0)
            first=git('rev-parse','HEAD',cwd=checkout)
            self.assertEqual(persist().returncode,0)
            self.assertEqual(git('rev-parse','HEAD',cwd=checkout),first)
            # Another writer advances main. Our next push must rebase safely.
            git('pull','--ff-only',cwd=other);(other/'unrelated.txt').write_text('keep me\n')
            git('add','unrelated.txt',cwd=other);git('commit','-m','other writer',cwd=other);git('push',cwd=other)
            log=checkout/'logs/taoyuan/run.json';log.parent.mkdir(parents=True);log.write_text('{}\n')
            result=persist();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual((checkout/'unrelated.txt').read_text(),'keep me\n')
            self.assertEqual(raw.read_bytes(),b'original\r\n')
            self.assertEqual(git('rev-parse','HEAD',cwd=checkout),git('rev-parse','refs/heads/main',cwd=remote))
            # Any accidental edit to an existing raw file must stop persistence.
            raw.write_bytes(b'changed')
            self.assertNotEqual(persist().returncode,0)


if __name__=='__main__':unittest.main()
