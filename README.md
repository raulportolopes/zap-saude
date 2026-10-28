# Zap-Saúde Mauá (protótipo)

**Simulador online:** https://zapsaude.mago.tech (arquivo em `docs/index.html`, publicado pelo GitHub Pages).

Bot de WhatsApp que mostra a espera nas 4 UPAs de Mauá (Barão de Mauá, Zaíra, Magini e Vila Assis), faz uma triagem rápida de sinais de alarme e indica a unidade com menor tempo total (deslocamento + espera). Aceita texto, botões e áudio, e responde em áudio quando recebe áudio.

**Os tempos de espera são simulados.** Mauá não publica a fila das UPAs na internet (pesquisado em 28/10/2026). Quando a prefeitura topar o piloto, a recepção de cada UPA informa a fila pelo endpoint `/admin/fila` (nível 1), e o dado real substitui a simulação automaticamente. Os tempos de deslocamento em `config/maua.json` também são estimativas iniciais para ajustar com a Secretaria de Saúde.

## Rodar no computador

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # ffmpeg também precisa estar instalado para a voz
python -m pytest -q                  # 10 testes do fluxo e do webhook
export WA_TOKEN=...                  # token da Cloud API (Meta)
export WA_PHONE_NUMBER_ID=...        # ID do número de teste
export WA_VERIFY_TOKEN=um-segredo-seu
export WA_APP_SECRET=...             # valida a assinatura das chamadas da Meta
export ADMIN_TOKEN=outro-segredo     # protege o painel de fila
uvicorn app.main:app --port 8000
```

## Ligar no WhatsApp (número de teste gratuito da Meta)

1. Em developers.facebook.com, crie um app do tipo Business e adicione o produto **WhatsApp**. A Meta dá um número de teste e permite cadastrar até 5 celulares para receber mensagens.
2. Copie o token temporário e o Phone number ID para as variáveis acima.
3. Exponha o servidor na internet (por exemplo `ngrok http 8000`) e cadastre `https://SEU-ENDERECO/webhook` como Callback URL, com o mesmo `WA_VERIFY_TOKEN`. Assine o campo **messages**.
4. Mande "oi", "UPA" ou um áudio do celular cadastrado para o número de teste.

## Informar a fila (nível 1)

```bash
curl -X POST http://localhost:8000/admin/fila \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"unidade": "magini", "amarelo": 20, "verde": 75, "azul": 110}'
```

`GET /fila` mostra o que o bot está usando e a origem de cada dado (`manual` ou `simulado`). Dado manual com mais de 30 minutos volta a ser tratado como ausente.

## Estrutura

| Arquivo | O que faz |
| --- | --- |
| `config/maua.json` | Unidades, regiões, deslocamentos e horário das UBS. Outra cidade = outro arquivo |
| `app/conversa.py` | Fluxo da conversa e recomendação, sem depender do WhatsApp |
| `app/fila.py` | Guarda a fila de cada UPA e gera a simulação |
| `app/whatsapp.py` | Monta e envia as mensagens pela Cloud API |
| `app/voz.py` | Transcreve áudio (faster-whisper) e gera a fala (gTTS + ffmpeg para OGG/OPUS) |
| `app/main.py` | Webhook, painel de fila e envio dos alertas "avise quando baixar" |

## Limites do protótipo

- Estado da conversa e alertas ficam na memória; em produção vão para o banco.
- A triagem é só uma lista de sinais de alarme e precisa ser validada por um médico regulador antes de ir ao ar.
- O texto da triagem e da UBS (horário 7h–17h) deve ser confirmado com a prefeitura.
