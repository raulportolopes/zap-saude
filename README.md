# Zap-Saúde Guarulhos (protótipo)

**Simulador online:** https://zapsaude.magox.tech (arquivo em `docs/index.html`, publicado pelo GitHub Pages).

Bot de WhatsApp que mostra a espera nas 11 unidades de urgência de Guarulhos (UPAs, prontos-socorros dos hospitais municipais e prontos-atendimentos), para adulto e infantil, e manda o caminho até a unidade escolhida: de ônibus (Google Maps no modo transporte público) ou de carro (Waze e Google Maps). Aceita texto, botões, áudio e localização, e responde em áudio quando recebe áudio.

## De onde vêm os dados

Do painel público da prefeitura, no SISS Online (o mesmo sistema do VAR da Saúde de Santo André):
https://guarulhos-portal.sissonline.com.br/servicos/tempo-espera-ps/estabelecimentos

O portal **não tem API JSON**: a página é montada no servidor e já traz os números no HTML. O `app/siss.py` lê essa página a cada 5 minutos (o mesmo ritmo da prefeitura) e extrai, por unidade e por painel (adulto e infantil), o tempo e a fila de cada classificação: muito urgente 🟠, urgente 🟡, pouco urgente 🟢, não urgente 🔵 e sem classificação ⚪. O portal não pede login nem token. Se ele cair, o bot continua com o último painel lido e avisa que os números podem estar desatualizados.

Para conferir o coletor no seu computador: `python -m app.siss`.

O portal não informa endereço nem coordenadas. Por isso `config/guarulhos.json` traz, para cada unidade, o código CNES, o endereço e `lat`/`lng`, tirados da API de dados abertos do CNES (Ministério da Saúde) e conferidos no OpenStreetMap em 29/10/2026. A coordenada do HMU veio do OpenStreetMap, porque a do CNES aponta para o centro da cidade. Com isso o link do mapa abre direto na unidade e, quando a pessoa manda a localização, o bot ordena as unidades por tempo de carro + espera.

Duas unidades estão marcadas com `"conferir": true` (UPA Taboão e UPA Cumbica), porque a coordenada não pôde ser confirmada no OpenStreetMap. Os links delas usam o endereço até alguém abrir o ponto no mapa e tirar a marca.

## Trajeto

| Como vai | O que o bot manda |
| --- | --- |
| Carro | Link do Waze e link do Google Maps no modo carro |
| Ônibus, sem chave do Google | Link do Google Maps no modo transporte público (o app calcula linhas, pontos e horários) |
| Ônibus, com `GOOGLE_MAPS_API_KEY` | Passo a passo dentro do WhatsApp ("pegue a linha 301 no ponto X, desça em Y"), pela Routes API do Google. O bot pede a localização com o botão nativo do WhatsApp |

## Rodar no computador

```bash
python -m venv .venv && source .venv/bin/activate   # no Windows: .venv\Scripts\activate
pip install -r requirements.txt     # ffmpeg também precisa estar instalado para a voz
python -m pytest -q                  # 20 testes do coletor, do fluxo e do webhook
export WA_TOKEN=...                  # token da Cloud API (Meta)
export WA_PHONE_NUMBER_ID=...        # ID do número de teste
export WA_VERIFY_TOKEN=um-segredo-seu
export WA_APP_SECRET=...             # valida a assinatura das chamadas da Meta
export GOOGLE_MAPS_API_KEY=...       # opcional: passo a passo de ônibus no WhatsApp
uvicorn app.main:app --port 8000
```

`GET /fila` mostra o último painel lido e o erro da última coleta, se houver.

## Colocar no ar na VPS

Veja o [DEPLOY.md](DEPLOY.md): Docker + Caddy (HTTPS automático), passo a passo para a VPS da Hostinger.

## Ligar no WhatsApp (número de teste gratuito da Meta)

1. Em developers.facebook.com, crie um app do tipo Business e adicione o produto **WhatsApp**. A Meta dá um número de teste e permite cadastrar até 5 celulares para receber mensagens.
2. Copie o token temporário e o Phone number ID para as variáveis acima.
3. Exponha o servidor na internet (por exemplo `ngrok http 8000`) e cadastre `https://SEU-ENDERECO/webhook` como Callback URL, com o mesmo `WA_VERIFY_TOKEN`. Assine o campo **messages**.
4. Mande "oi", "UPA", o nome de uma unidade ou um áudio do celular cadastrado para o número de teste.

## Estrutura

| Arquivo | O que faz |
| --- | --- |
| `config/guarulhos.json` | Nome amigável, apelidos, CNES, endereço e coordenadas de cada unidade |
| `app/siss.py` | Lê o painel do SISS Online e transforma o HTML em dados |
| `app/conversa.py` | Fluxo da conversa, sem depender do WhatsApp |
| `app/rotas.py` | Links do Waze e do Google Maps e passo a passo de ônibus (Routes API) |
| `app/whatsapp.py` | Monta e envia as mensagens pela Cloud API |
| `app/voz.py` | Transcreve áudio (faster-whisper) e gera a fala (gTTS + ffmpeg para OGG/OPUS) |
| `app/main.py` | Webhook e coleta do portal a cada 5 minutos |
| `tests/fixtures/` | Trecho literal da página do portal (2 unidades) e a página completa remontada com o retrato de 29/10/2026 09:34 |

## Limites do protótipo

- Estado da conversa fica na memória; em produção vai para o banco.
- Se a prefeitura mudar o layout da página, o coletor para de ler e registra o erro (o teste `test_layout_desconhecido_gera_erro` cobre isso).
- O bot não substitui a triagem: com sinais de alarme ele manda ligar 192.
