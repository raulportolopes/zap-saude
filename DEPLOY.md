# Colocar o Zap-Saúde no ar na VPS (Hostinger KVM 2)

O bot roda em Docker, com o Caddy na frente cuidando do HTTPS. Outros projetos podem dividir a mesma VPS: cada um ganha um subdomínio e um bloco no `Caddyfile`.

Os exemplos usam o endereço `bot.magox.tech`. Troque se preferir outro, mas não use `zapsaude.magox.tech`, que é o simulador no GitHub Pages.

## 1. Apontar o subdomínio para a VPS

No painel onde o domínio magox.tech é gerenciado (Hostinger > Domínios > DNS), crie um registro:

| Tipo | Nome | Valor |
| --- | --- | --- |
| A | `bot` | `179.199.155.128` |

Leva de alguns minutos a uma hora para valer.

## 2. Entrar na VPS

No Windows, abra o PowerShell:

```bash
ssh root@179.199.155.128
```

A senha de root fica no painel da Hostinger, em VPS > Gerenciar > Acesso SSH.

## 3. Conferir a VPS

```bash
# a VPS precisa alcançar o portal da prefeitura (esperado: HTTP/1.1 200 OK)
curl -sI https://guarulhos-portal.sissonline.com.br/servicos/tempo-espera-ps/estabelecimentos | head -1

# as portas 80 e 443 precisam estar livres; se aparecer alguma linha, outro programa já usa a porta
ss -tlnp | grep -E ':(80|443) '
```

Se as portas 80 e 443 já estiverem em uso (por exemplo, por um n8n instalado pelo catálogo da Hostinger, que vem com o próprio proxy), não siga adiante: o bot precisa entrar no proxy que já existe, e o passo 6 muda.

## 4. Instalar o Docker

```bash
curl -fsSL https://get.docker.com | sh
ufw allow 80,443/tcp 2>/dev/null; ufw allow OpenSSH 2>/dev/null
```

Se o firewall do painel da Hostinger estiver ativo, libere também as portas 80 e 443 lá.

## 5. Baixar o projeto e preencher as senhas

```bash
git clone https://github.com/raulportolopes/zap-saude.git
cd zap-saude
cp .env.example .env
nano .env        # preencha; para salvar: Ctrl+O, Enter, Ctrl+X
```

Para o primeiro teste, basta `DOMINIO` e `WA_VERIFY_TOKEN`. Os dados do WhatsApp entram no passo 7.

## 6. Subir

```bash
docker compose up -d --build
docker compose logs -f bot      # Ctrl+C para sair dos logs
```

Abra `https://bot.magox.tech/fila` no navegador. Deve aparecer a lista das 11 unidades com os tempos de agora. Se aparecer `"erro"`, a VPS não conseguiu ler o portal.

## 7. Ligar no WhatsApp

1. Em developers.facebook.com, crie um app do tipo **Business** e adicione o produto **WhatsApp**.
2. Em WhatsApp > Configuração da API, copie o **token** e o **Phone number ID** para o `.env` (`WA_TOKEN` e `WA_PHONE_NUMBER_ID`). Em Configurações do app > Básico, copie a **Chave secreta do app** para `WA_APP_SECRET`.
3. Cadastre o seu celular como destinatário de teste.
4. Rode `docker compose up -d` de novo para o bot ler o `.env` novo.
5. Em WhatsApp > Configuração, cadastre o webhook:
   - URL de callback: `https://bot.magox.tech/webhook`
   - Token de verificação: o mesmo `WA_VERIFY_TOKEN` do `.env`
   - Assine o campo **messages**.
6. Mande "oi" do seu celular para o número de teste.

O token da tela de teste vale 24 horas. Para o bot ficar no ar de vez, gere um token permanente no Meta Business Manager (Configurações do negócio > Usuários do sistema > Gerar token, com as permissões `whatsapp_business_messaging` e `whatsapp_business_management`) e troque no `.env`.

## Atualizar depois de uma mudança no código

```bash
cd ~/zap-saude
git pull
docker compose up -d --build
```

## Comandos úteis

| Para | Comando |
| --- | --- |
| Ver se está rodando | `docker compose ps` |
| Ver os logs | `docker compose logs -f bot` |
| Reiniciar | `docker compose restart bot` |
| Parar tudo | `docker compose down` |

## Outro projeto na mesma VPS

Coloque o outro serviço no `docker-compose.yml` (ou em outro compose na mesma rede), crie o registro DNS do subdomínio e acrescente um bloco no `Caddyfile`:

```
n8n.magox.tech {
	reverse_proxy n8n:5678
}
```

Depois rode `docker compose restart caddy`.
