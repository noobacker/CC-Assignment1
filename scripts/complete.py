"""Collect data first, deploy, verify SES, import, and exercise the real application."""
import argparse
import os
import re
import subprocess
import sys
import time
import boto3
from settings import ROOT, load_env

def run(*args):
    subprocess.run([sys.executable, str(ROOT / 'scripts' / args[0]), *args[1:]], check=True, cwd=ROOT)

def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-email', action='store_true', help='Request an SES verification email for your configured sender/recipient')
    args = parser.parse_args()
    sender = os.environ.get('SES_SENDER_EMAIL')
    recipient = os.environ.get('TEST_RECIPIENT_EMAIL') or sender
    missing = [name for name in ['SES_SENDER_EMAIL', 'YELP_API_KEY'] if not os.environ.get(name)]
    if missing: raise SystemExit('Fill these fields in .env first: ' + ', '.join(missing))
    session = boto3.Session(region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))
    session.client('sts').get_caller_identity()
    run('ingest_yelp.py', '--collect-only')  # No billable domain until real data meets rubric.
    if args.verify_email:
        ses = session.client('ses')
        for email in sorted({sender, recipient}):
            status = ses.get_identity_verification_attributes(Identities=[email])['VerificationAttributes']
            if status.get(email, {}).get('VerificationStatus') != 'Success':
                ses.verify_email_identity(EmailAddress=email)
        print('Click the SES verification link(s) in your inbox. Waiting up to 15 minutes.', flush=True)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            identities = ses.get_identity_verification_attributes(Identities=list({sender, recipient}))['VerificationAttributes']
            if all(identities.get(email, {}).get('VerificationStatus') == 'Success' for email in {sender, recipient}): break
            time.sleep(15)
        else: raise SystemExit('Email verification is pending. Click the link(s), then rerun this command.')
    else:
        ses = session.client('ses')
        identities = ses.get_identity_verification_attributes(Identities=list({sender, recipient}))['VerificationAttributes']
        if any(identities.get(email, {}).get('VerificationStatus') != 'Success' for email in {sender, recipient}):
            raise SystemExit('Sender/test recipient must be verified. Rerun with --verify-email to request the verification links.')
    run('deploy.py')
    run('ingest_yelp.py', '--load-only')
    run('verify_live.py', '--email', recipient)
    readme = ROOT / 'README.md'
    text = re.sub(r'\*\*Current status:\*\*.*?(?=\n\nWebsite:|\n\n## (?:Finish|Reproduce) setup)',
        '**Current status:** AWS deployment, genuine Yelp import and automated live verification completed. '
        'See `data/live-verification.json` for results. SES accepted the test emails; '
        'confirm inbox delivery and collect the submission evidence before submitting.',
        readme.read_text(), flags=re.S)
    readme.write_text(text)
    run('package.py')
    print('Live verification completed. Confirm both recommendation emails arrived and capture your submission evidence.')

if __name__ == '__main__': main()
