# Colocar o Zap-Saúde no ar na VPS (Hostinger KVM 2)

O bot roda em Docker e só escuta dentro da própria VPS (`127.0.0.1:8000`). O Nginx que já está instalado na VPS publica o bot com HTTPS, assim como pode publicar outros projetos: cada um ganha um subdomínio e um arquivo em `/etc/nginx/sites-available`.

Conferido em 29/10/2026 na VPS srv2049757: ela acessa o portal da prefeitura (HTTP 200) e o Nginx ocupa as portas 80 e 443.

Os exemplos usam o endereço `bot.magox.tech`. Troque se preferir outro, mas não use `zapsaude.magox.tech`, que é o simulador no GitHub Pages.

## 1. Apontar o subdomínio para a VPS

No painel onde o domínio magox.tech é gerenciado (Hostinger > Domínios > DNS), crie um registro:

| Tipo | Nome | Valor |
| --- | --- | --- |
| A | `bot` | `179.199.155.128` |

Leva de alguns minutos a uma hora para valer.

## 2. Entrar na VPS

No painel da Hostinger, em VPS > Gerenciar, clique em **Web console** (canto superior direito). Abre um terminal já logado como root. Para colar um comando, use o botão direito do mouse ou Ctrl+Shift+V.

Outra opção é o PowerShell do Windows: `ssh root@179.199.155.128`, com a senha de root do painel.

## 3. Conferir a VPS

```bash
# a VPS precisa alcançar o portal da prefeitura (esperado: HTTP/1.1 200 OK)
curl -sI https://guarulhos-portal.sissonline.com.br/servicos/tempo-espera-ps/estabelecimentos | head -1

# quem usa as portas 80 e 443 (na VPS da Hostinger, o Nginx)
ss -tlnp | grep -E ':(80|443) '
```

Se aparecer `nginx`, siga este guia como está. Se a VPS não tiver nada nessas portas, você pode usar o Caddy incluído no projeto: no passo 6 rode `docker compose --profile caddy up -d --build` e pule o passo 7.

## 4. Instalar o Docker

Veja primeiro se já está instalado (o painel da Hostinger tem um Gerenciador Docker):

```bash
docker compose version
```

Se aparecer uma versão, pule para o passo 5. Se der "command not found":

```bash
curl -fsSL https://get.docker.com | sh
```

## 5. Baixar o projeto e preencher as senhas

```bash
git clone https://github.com/raulportolopes/zap-saude.git
cd zap-saude
cp .env.example .env
nano .env        # preencha; para salvar: Ctrl+O, Enter, Ctrl+X
```

Para o primeiro teste, basta inventar uma senha em `WA_VERIFY_TOKEN`. Os dados do WhatsApp entram no passo 8.

## 6. Subir o bot

```bash
docker compose up -d --build
docker compose logs -f bot      # Ctrl+C para sair dos logs
curl -s http://127.0.0.1:8000/fila | head -c 300; echo
```

O último comando deve mostrar o começo da lista das unidades com os tempos de agora. Se aparecer `"erro"`, a VPS não conseguiu ler o portal.

## 7. Publicar com HTTPS no Nginx

```bash
cp deploy/nginx-bot.conf /etc/nginx/sites-available/bot.magox.tech
ln -s /etc/nginx/sites-available/bot.magox.tech /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# certificado HTTPS gratuito (Let's Encrypt); o DNS do passo 1 já precisa estar valendo
apt install -y certbot python3-certbot-nginx
certbot --nginx -d bot.magox.tech
```

`nginx -t` precisa responder "syntax is ok" e "test is successful" antes do reload; se não responder, pare e me mande a mensagem. O certbot pergunta um e-mail e se aceita os termos. Depois disso, `https://bot.magox.tech/fila` deve abrir no navegador.

## 8. Ligar no WhatsApp

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

Suba o projeto escutando em outra porta local (por exemplo `127.0.0.1:5678`), copie `deploy/nginx-bot.conf` com outro nome, troque o `server_name` e a porta do `proxy_pass`, crie o registro DNS do subdomínio e repita o passo 7 com esse nome.
