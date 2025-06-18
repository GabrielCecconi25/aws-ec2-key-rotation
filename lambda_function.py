import boto3
import base64
import paramiko
import hashlib
import os
import csv
from utils.ec2_utils import get_linux_instances_without_asg
from utils.ssm_utils import execute_command, get_ssm_response, is_ssm_online
from utils.secrets_utils import create_secret_backup
from utils.s3_utils import upload_csv_report
from utils.sns_utils import publish_report_notification

REGION = os.environ.get("AWS_REGION", "us-east-1")
ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
secretsmanager = boto3.client("secretsmanager", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
sts = boto3.client("sts")
ACCOUNT_ID = sts.get_caller_identity()["Account"]
BUCKET_NAME = os.environ.get("BACKUP_BUCKET_NAME")
valid_users = ["ec2-user", "ubuntu", "centos", "admin", "fedora", "bitnami"]

def generate_key_pair(key_name_old):
    key = paramiko.RSAKey.generate(2048)
    private_key = key.key.private_bytes(
        encoding=paramiko.util.ENCODING_PEM,
        format=paramiko.util.PEM_PKCS8,
        encryption_algorithm=paramiko.util.NoEncryption()
    ).decode()
    public_key = f"{key.get_name()} {key.get_base64()} {key_name_old}"
    return private_key, public_key

def lambda_handler(event, context):
    instances = get_linux_instances_without_asg(ec2)
    report_data = []

    print(f"▶️ Starting SSH key rotation for instances in region {REGION}...")
    for instance in instances:
        instance_id = instance["InstanceId"]
        key_name = instance.get("KeyName")
        name = next((tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"), "unknown")
        status = "skipped"
        user_found = ""

        # Initial checks
        if not key_name:
            report_data.append([instance_id, status, user_found, key_name, "Instance without SSH Key"])
            continue
        if instance["State"]["Name"] != "running":
            report_data.append([instance_id, status, user_found, key_name, "Instance is not Runnging"])
            continue
        if not is_ssm_online(ssm, instance_id):
            report_data.append([instance_id, status, user_found, key_name, "SSM Agent offline"])
            continue


        print(f"🔄 Instância: {instance_id} ({name})")

        private_key, public_key = generate_key_pair()

        cmd_id = execute_command(ssm, instance_id, "ls /home")
        users = get_ssm_response(ssm, cmd_id, instance_id).splitlines()

        # Check if users are valid
        if users not in valid_users:
            report_data.append([instance_id, status, user_found, key_name, "No valid user found"])
            continue
        # Loop users
        for user in users:
            if user not in valid_users:
                continue

            path = f"/home/{user}/.ssh/authorized_keys"
            try:
                cmd_id = execute_command(ssm, instance_id, f"cat {path}")
                content = get_ssm_response(ssm, cmd_id, instance_id)
                backup_hash = hashlib.sha256(content.encode()).hexdigest()

                if content.strip():
                    for content in content.strip():
                        if 
                    s3.put_object(
                        Bucket=BUCKET_NAME,
                        Key=f"backups-secrets/{name}_{instance_id}_{user}.authorized_keys",
                        Body=content.encode()
                    )

                execute_command(ssm, instance_id, f"sed -i \"/{key_name}/c\\\\{public_key}\" {path} ")
                user_found = user
                status = "updated"
                break
            except Exception as e:
                continue

        secretsmanager.create_secret(Name=f"{key_name}.pem", SecretString=private_key)
        secretsmanager.create_secret(Name=f"{key_name}.pub", SecretString=public_key)

        report_data.append([instance_id, status, user_found])

    print("⏹️ SSH key rotation completed.")

    # Report CSV
    report_path = "/tmp/ssh_rotation_report"
    with open(report_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["InstanceId", "Status", "User", "KeyName", "Coment"])
        writer.writerows(report_data)

    # Notification System
    report_key = upload_csv_report(BUCKET_NAME, report_path)
    report_url = f"https://s3.amazonaws.com/{BUCKET_NAME}/{report_key}"

    publish_report_notification(
        topic_arn=os.environ["SNS_TOPIC_ARN"],
        subject="🔐 Relatório de rotação SSH EC2",
        message="A rotação de chaves SSH foi concluída com sucesso.",
        report_link=report_url
    )

    return {"statusCode": 200, "body": "Chaves rotacionadas com sucesso"}
