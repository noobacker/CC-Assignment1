import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
os.environ.update({'AWS_DEFAULT_REGION': 'us-east-1', 'AWS_EC2_METADATA_DISABLED': 'true',
                   'STATE_TABLE': 'test-state', 'RESTAURANTS_TABLE': 'test-restaurants',
                   'QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123/test.fifo',
                   'SES_SENDER_EMAIL': 'sender@example.com', 'LEX_BOT_ID': 'TESTBOT123', 'LEX_ALIAS_ID': 'TESTALIAS1'})
