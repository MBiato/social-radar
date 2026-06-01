#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Social Radar — Setup da GCP VM (e2-micro, Ubuntu 22.04)
# Execute uma vez após criar a VM:
#   bash setup_vm.sh
# ─────────────────────────────────────────────────────────────────

set -e

REPO_URL="https://github.com/SEU_USUARIO/social-radar.git"  # ← altere
REPO_DIR="$HOME/social-radar"
GIT_NAME="Social Radar Bot"
GIT_EMAIL="seu@email.com"  # ← altere

echo ">>> Atualizando sistema..."
sudo apt-get update -q && sudo apt-get install -y -q python3-pip git

echo ">>> Clonando repositório..."
if [ ! -d "$REPO_DIR" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
else
  echo "    (repositório já existe)"
fi

echo ">>> Configurando git..."
git -C "$REPO_DIR" config user.name  "$GIT_NAME"
git -C "$REPO_DIR" config user.email "$GIT_EMAIL"

# Para push automático sem senha, use SSH ou um Personal Access Token (PAT)
# Opção PAT: git remote set-url origin https://TOKEN@github.com/SEU_USUARIO/social-radar.git
# Opção SSH: adicione a chave pública da VM nas SSH Keys do GitHub

echo ">>> Instalando dependências Python..."
pip3 install -q -r "$REPO_DIR/collector/requirements.txt"

echo ">>> Criando arquivo .env..."
if [ ! -f "$REPO_DIR/collector/.env" ]; then
  cp "$REPO_DIR/collector/.env.example" "$REPO_DIR/collector/.env"
  echo ""
  echo "  ⚠  Preencha as variáveis em: $REPO_DIR/collector/.env"
fi

echo ">>> Criando diretório de dados..."
mkdir -p "$REPO_DIR/data"
if [ ! -f "$REPO_DIR/data/metrics.json" ]; then
  echo '{}' > "$REPO_DIR/data/metrics.json"
  git -C "$REPO_DIR" add data/ && git -C "$REPO_DIR" commit -m "data: init" --allow-empty
fi

echo ">>> Criando arquivo de log..."
sudo touch /var/log/sr-collector.log
sudo chown $USER /var/log/sr-collector.log

echo ">>> Configurando cron (07h00 diariamente)..."
# Adiciona ao crontab sem duplicar
(crontab -l 2>/dev/null | grep -v 'sr-collector'; echo "0 7 * * * cd $REPO_DIR && python3 collector/collector.py >> /var/log/sr-collector.log 2>&1") | crontab -

echo ""
echo "═══════════════════════════════════════════════"
echo "  Setup concluído!"
echo ""
echo "  Próximos passos:"
echo "  1. Preencha: $REPO_DIR/collector/.env"
echo "  2. Configure push automático (PAT ou SSH key)"
echo "  3. Teste: cd $REPO_DIR && python3 collector/collector.py"
echo "  4. Verifique o log: tail -f /var/log/sr-collector.log"
echo "═══════════════════════════════════════════════"
