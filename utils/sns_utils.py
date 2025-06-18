import boto3
import json

sns = boto3.client("sns")

def publish_report_notification(topic_arn, subject, message, report_link=None):
    if report_link:
        message += f"\n\n📎 Link do relatório: {report_link}"
    sns.publish(
        TopicArn=topic_arn,
        Subject=subject,
        Message=message
    )
