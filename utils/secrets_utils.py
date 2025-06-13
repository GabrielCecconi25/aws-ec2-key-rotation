def get_secret_value(secret_name, secretsmanager):
    response = secretsmanager.get_secret_value(SecretId=secret_name)
    return response["SecretString"]

def create_secret_backup(secretsmanager, name, value):
    secretsmanager.create_secret(
        Name=name,
        SecretString=value
    )
