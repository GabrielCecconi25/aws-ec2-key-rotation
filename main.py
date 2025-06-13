import boto3
import csv
import base64
import os
import hashlib
from utils.ec2_utils import get_linux_instances_without_asg
from utils.ssm_utils import execute_command, get_ssm_response, is_ssm_online
from utils.secrets_utils import get_secret_value, create_secret_backup
from datetime import datetime

REGION = "us-east-1"
ACCOUNT_NAME = "Sandbox"  # opcional
SECRETS_REUTILIZADAS = {}  # hash do conteúdo -> nome da secret

ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
secretsmanager = boto3.client("secretsmanager", region_name=REGION)
sts = boto3.client("sts")
ACCOUNT_ID = sts.get_caller_identity()["Account"]

BACKUPS = []
INPUT_FILE = "input/keys_rotation.csv"
OUTPUT_FILE = "output/ssh_rotation_report.csv"


def carregar_instancias_processadas(path):
    processadas = set()
    if not os.path.exists(path):
        return processadas

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            processadas.add((row["InstanceId"], row["Region"], row["Account ID"]))
    return processadas


def gerar_nome_unico_secret(base_name):
    """Gera um nome único para secret no Secrets Manager"""
    i = 1
    secret_name = base_name
    while True:
        try:
            secretsmanager.describe_secret(SecretId=secret_name)
            # já existe, tenta outro
            secret_name = f"{base_name}-{i}"
            i += 1
        except secretsmanager.exceptions.ResourceNotFoundException:
            break
    return secret_name


def process_instance(instance, chave_antiga, chave_nova, chave_antiga_name):
    instance_id = instance["InstanceId"]
    name = next((tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"), "")
    private_ip = instance.get("PrivateIpAddress", "")
    key_name = instance.get("KeyName", "")
    instance_type = instance["InstanceType"]
    print(f"🔄 Processando instância: {name} ({instance_id})")

    status = "ERROR"
    comentario = ""
    usuario_encontrado = ""
    secret_backup = ""

    if instance["State"]["Name"] != "running":
        comentario = "Instância Stoped"
    elif not is_ssm_online(ssm, instance_id):
        comentario = "SSM Agent Offline"
    else:
        cmd_ls_home = "ls /home"
        cmd_id = execute_command(ssm, instance_id, cmd_ls_home)
        output = get_ssm_response(ssm, cmd_id, instance_id)

        usuarios_home = [u for u in output.splitlines() if u.strip()]
        usuarios_preferenciais = ["ec2-user", "ubuntu", "centos", "admin"]
        usuarios_validos = [u for u in usuarios_preferenciais if u in usuarios_home]

        if not usuarios_validos:
            comentario = "Sem usuário padrão na maquina"
        else:
            chave_encontrada = False

            for usuario in usuarios_validos:
                path_key = f"/home/{usuario}/.ssh/authorized_keys"
                cmd_cat_key = f"cat {path_key}"
                cmd_id = execute_command(ssm, instance_id, cmd_cat_key)
                chave_atual = get_ssm_response(ssm, cmd_id, instance_id)

                if chave_antiga not in chave_atual:
                    continue

                usuario_encontrado = usuario
                chave_encontrada = True

                if chave_atual.strip() == chave_antiga.strip():
                    # Simples substituição
                    cmd_write_new = f"echo '{chave_nova}' > {path_key}"
                    execute_command(ssm, instance_id, cmd_write_new)
                    secret_backup = chave_antiga_name
                    status = "OK"
                else:
                    # Caso com múltiplas chaves, criar backup
                    hash_chave = hashlib.sha256(chave_atual.encode()).hexdigest()

                    if hash_chave in SECRETS_REUTILIZADAS:
                        # Já criada, reutilizar
                        secret_backup = SECRETS_REUTILIZADAS[hash_chave]
                    else:
                        base_name = f"{name}-{usuario}".replace(" ", "_")
                        nome_secret = gerar_nome_unico_secret(base_name)
                        create_secret_backup(secretsmanager, nome_secret, chave_atual)
                        BACKUPS.append(nome_secret)
                        SECRETS_REUTILIZADAS[hash_chave] = nome_secret
                        secret_backup = nome_secret

                    # Substituir por chave nova
                    cmd_write_new = f"echo '{chave_nova}' > {path_key}"
                    execute_command(ssm, instance_id, cmd_write_new)
                    status = "OK"

                break  # parar após primeiro usuário válido

            if not chave_encontrada:
                comentario = f"Chave antiga não encontrada no(s) usuário(s): {usuarios_validos}"

    return {
        "Status": status,
        "Usuário": usuario_encontrado,
        "Secret Backup": secret_backup,
        "Region": REGION,
        "Account ID": ACCOUNT_ID,
        "Account Name": ACCOUNT_NAME,
        "Name": name,
        "InstanceId": instance_id,
        "PrivateIpAddress": private_ip,
        "InstanceType": instance_type,
        "KeyName": key_name,
        "Comentário": comentario
    }


def main():
    os.makedirs("output", exist_ok=True)
    resultados = []
    instances = get_linux_instances_without_asg(ec2)

    with open(INPUT_FILE, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            chave_antiga_name = row["chave_antiga"]
            chave_nova_name = row["chave_nova"]

            try:
                chave_antiga = get_secret_value(chave_antiga_name, secretsmanager)
                chave_nova = get_secret_value(chave_nova_name, secretsmanager)
            except Exception as e:
                print(f"[ERRO] Não foi possível buscar secrets: {chave_antiga_name} ou {chave_nova_name} → {e}")
                continue

            print(f"\n🔁 Rotacionando {chave_antiga_name} -> {chave_nova_name}")

            instancias_processadas = carregar_instancias_processadas(OUTPUT_FILE)

            for instance in instances:
                instance_key = (instance["InstanceId"], REGION, ACCOUNT_ID)
                if instance_key in instancias_processadas:
                    print(f"⏩ Instância {instance['InstanceId']} já processada — ignorando.")
                    continue

                resultado = process_instance(instance, chave_antiga, chave_nova, chave_antiga_name)
                resultados.append(resultado)

    file_exists = os.path.exists(OUTPUT_FILE)

    with open(OUTPUT_FILE, "a", newline="") as csvfile:
        campos = ["Status", "Usuário", "Secret Backup", "Region", "Account ID", "Account Name",
                  "Name", "InstanceId", "PrivateIpAddress", "InstanceType", "KeyName", "Comentário"]
        writer = csv.DictWriter(csvfile, fieldnames=campos)
        if not file_exists:
            writer.writeheader()
        writer.writerows(resultados)

    print(f"\n✅ ROTAÇÃO FINALIZADA. Relatório salvo em: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
