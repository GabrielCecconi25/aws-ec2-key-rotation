import boto3
import os
import datetime

s3 = boto3.client("s3")


def upload_file_to_s3(bucket_name, key, file_path):
    s3.upload_file(file_path, bucket_name, key)


def upload_csv_report(bucket_name, report_file, prefix="reports"):
    filename = os.path.basename(report_file)
    key = f"{prefix}/{filename-datetime.datetime.now().strftime('%Y%m%d')}.csv"
    upload_file_to_s3(bucket_name, key, report_file)
    return key
