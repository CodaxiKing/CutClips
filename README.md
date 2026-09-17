# ClipForge

Vídeo longo → clipes verticais → revisão → pacote de publicação → resultados.
Aplicativo local em Python, FastAPI, SQLite e FFmpeg. Interface sem framework.

## O que você pode fazer

- Importar um arquivo de vídeo ou link do YouTube e informar nicho, público e créditos.
- Transcrever uma vez, preservar pausas, perguntas, ênfase e trocas de falante, e reutilizar o cache nas análises e edições.
- Resumir vídeos longos em blocos temáticos de 3–5 minutos antes de analisar somente os blocos mais promissores.
- Selecionar trechos por IA ou heurística. A IA escolhe IDs de frases; o código calcula os segundos.
- Classificar o tipo do vídeo (podcast, entrevista, tutorial, aula, vlog, gameplay, notícia ou review) na própria triagem e ajustar as regras de corte a ele. Trechos que dividem frases com outro de nota maior são encurtados, não descartados, e emendas curtas entre os blocos analisados entram inteiras.
- Reavaliar os trechos finais depois do ajuste de duração. A nota é editorial, não uma previsão de visualizações.
- Saber por que a IA não foi usada quando isso acontece: o projeto avisa em destaque e diz o que corrigir, em vez de trocar por heurística em silêncio.
- Revisar início/fim no vídeo original e corrigir cada palavra, mantendo seus timestamps.
- Renderizar apenas o clipe alterado, reutilizando a transcrição. A versão anterior fica disponível.
- Escolher enquadramento manual, rastreamento de rosto, estimativa de fala, tela dividida ou imagem inteira com fundo desfocado.
- Cortar transmissões da Twitch, Kick e YouTube por link: VOD, clipe ou live em andamento, com saída 16:9 ou 9:16.
- Montar um Top 3, Top 4 ou Top 5 com links do TikTok, título no topo e ranking no meio da lateral esquerda, sem barras de fundo. Escolha fontes, cores, tamanhos, contorno, sombra ou brilho e revelação suave dos nomes. Defina início e fim de cada trecho, anime a entrada do título, números e nomes e inclua seu @ com opacidade ajustável na parte inferior. Use “Editar e criar versão” no resultado para reutilizar e ajustar a configuração.
- Ajustar tamanho, posição, quantidade de palavras e estilo das legendas. Palavras de baixa confiança são sinalizadas.
- Normalizar volume, controlar picos e reduzir ruído opcionalmente.
- Usar automaticamente NVENC, Intel QSV ou AMD AMF, com fallback para `libx264`.
- Remover pausas internas longas e vícios isolados, mantendo áudio, vídeo, câmera e legendas sincronizados.
- Escolher entre três capas e revisar título, descrição, notas e data planejada.
- Organizar clipes como rascunhos, aprovados ou publicados.
- Baixar MP4, SRT, capa, texto para publicação e manifesto em ZIP, por clipe ou apenas os aprovados.
- Registrar métricas acumuladas manualmente ou importar o modelo CSV. O painel usa o último registro de cada versão, sem somar snapshots anteriores.

## Rodar no Windows

É necessário Python 3.11+ e FFmpeg/FFprobe com libass no PATH.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Crie `.env` a partir de `.env.example` se ainda não existir. O aplicativo carrega esse arquivo automaticamente, preservando variáveis já definidas no ambiente.

Em dois terminais:

```powershell
# Terminal 1: interface
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode api

# Terminal 2: fila de processamento e edições
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode worker
```

Abra http://127.0.0.1:8000. A API e o worker devem usar o mesmo `CLIPFORGE_STORAGE`.
O script também encontra o FFmpeg instalado pelo pacote opcional `static-ffmpeg` dentro de `.venv`.

No Windows, a opção mais simples é dar dois cliques em **Iniciar ClipForge.cmd**.
Ele inicia a API e o worker em segundo plano e abre o endereço correto. Não abra
`web/index.html` diretamente: a página depende da API. Se isso acontecer, ela
agora redireciona automaticamente para `http://127.0.0.1:8000/`.

Sem chave de IA, selecione **Sem IA**. Para Claude ou OpenAI, configure a chave do provedor no ambiente. Você pode especificar o modelo na interface. Ollama exige serviço local e modelo instalado.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

A API recebe uploads e serve arquivos; o worker executa download, transcrição e renderização. Para uso pessoal, mantenha a interface acessível apenas em sua máquina ou rede confiável: não há autenticação de usuários.

## Fluxo de trabalho

1. Envie um vídeo e informe assunto e público. Use conteúdo próprio ou para o qual tenha os direitos necessários.
2. Confira o método real de seleção e eventuais avisos nos clipes gerados.
3. Abra **Revisar e editar**, marque os limites, corrija palavras e ajuste o layout.
4. Salve e aguarde o worker. A renderização usa uma nova versão; em caso de falha, a anterior continua disponível.
5. Em **Preparar publicação**, ajuste título/descrição, selecione capa e aprove.
6. Baixe o pacote e publique pelo YouTube Studio. Registre o link após publicar.
7. Use **Registrar resultados** para acompanhar visualizações, porcentagem média assistida, inscritos, receita em reais e minutos de produção.

A data planejada organiza o trabalho localmente; não agenda um upload no YouTube.
A publicação continua manual. A aba Análise do canal oferece conexão Google para sincronizar métricas do canal; os resultados por versão de clipe no painel antigo continuam sendo registrados separadamente.
A seleção de miniatura de Shorts depende dos recursos disponíveis no próprio YouTube.

### Métricas e versões

Use valores acumulados, não apenas o incremento do dia. Para corrigir um registro, envie novamente a mesma data e versão. Campos desconhecidos ficam vazios: ausência de receita não significa receita zero.

O CSV aceita as colunas do botão **Modelo CSV**, em UTF-8, separado por vírgulas, datas `AAAA-MM-DD` e números decimais com ponto. Limites: 2 MB e 1.000 linhas. Todo o arquivo é validado antes da gravação.

Uma nova edição volta a ser rascunho. Os arquivos, metadados de publicação e métricas da versão anterior são preservados. O editor permite baixar vídeos anteriores; o painel identifica cada versão.

### Cortes de transmissão (Twitch, Kick, YouTube)

A aba **Cortes de live** aceita VOD, clipe e canal ao vivo das três plataformas. Transmissão em andamento é gravada a partir de agora pela janela escolhida — a gravação roda em tempo real, então 30 minutos pedidos são 30 minutos de espera. VOD e clipe são baixados normalmente.

Acima de `CLIPFORGE_PROSPECT_AFTER_MINUTES` (25 min por padrão) o vídeo passa por **garimpo** antes da transcrição: uma varredura só de áudio mede o quanto cada instante sobe acima do normal daquele trecho, soma as mudanças de cena e devolve os picos. Só as janelas em volta desses picos vão para o Whisper. Numa VOD de 6h isso troca horas de GPU por alguns minutos, e é o que torna o corte de live viável. Transmissão sem nenhuma reação (tutorial, música) cai para sondagens espalhadas pelo vídeo.

O formato de saída é escolhido por projeto: **16:9 horizontal** para o YouTube, que mantém o quadro inteiro da gameplay sem reenquadrar nada, ou **9:16 vertical** para Shorts, que usa o rastreamento de rosto. O manifesto registra os momentos garimpados, quantos segundos foram analisados e a orientação usada.

### Descobrir tendências e reunir candidatos

A aba **Descobrir** abre a central oficial de tendências do TikTok e oferece buscas de dança por música, coreografia e país. As tendências são consultadas no site oficial, que pode exigir login; não há importação automática de rankings nem um ranking mundial agregado. O país da busca é acrescentado ao texto pesquisado, sem garantir a localização dos criadores.

Cole links de vídeos, dê nomes para o ranking e organize candidatos por coleção. A lista fica no armazenamento deste navegador (e deste endereço/porta). Selecione de três a cinco candidatos, ajuste a ordem e use **Usar no ranking** para preencher o editor. Um ranking já preenchido pede confirmação antes da substituição. A montagem só começa quando você confirma a geração no editor.

### Montagem Top 5 de TikTok

Em **Áudio e autorização deste trecho**, cada posição permite manter, silenciar ou substituir o áudio, ajustar seu volume e enviar uma narração opcional. A redução automática de fundo atua enquanto a narração toca. Arquivos WAV/MP3/M4A/OGG/FLAC/AAC têm limite de 25 MB: os primeiros 15 segundos são preparados localmente; a montagem corta à duração do trecho ou completa com silêncio. A narração começa no início do trecho. Esses controles não geram voz automaticamente.

O editor consulta o histórico dos projetos existentes por link/ID e avisa sobre repetições antes de gerar. Novas montagens também guardam SHA-256 das fontes para comparar arquivos idênticos na revisão; links curtos distintos, arquivos recodificados e projetos excluídos limitam a comparação.

Os botões **Revisar e baixar** mostram duração, resolução, FPS, presença de áudio, fontes, autorizações declaradas, avisos e histórico antes do download. Autorizações podem ser atualizadas na revisão. Não há consulta ao Content ID nem previsão de alcance ou confirmação automática de licenças.

No resultado, **Acompanhamento após publicar** guarda consultas manuais por data: link do YouTube, restrições, visualizações totais e pelo feed, exibições no feed, porcentagem média assistida e porcentagem que continuou assistindo. São snapshots acumulados: a mesma data atualiza o registro; datas diferentes não são somadas. Campos desconhecidos permanecem vazios. Esses registros são próprios do ranking, separados do painel legado de resultados por versão de clipe.

A aba **Top 5** recebe cinco links públicos de vídeos do TikTok (inclusive `vm.tiktok.com` e `vt.tiktok.com`), uma frase que fica no topo do vídeo inteiro e um nome para cada posição. O resultado é um MP4 vertical 1080×1920 a 30 fps com os cinco trechos em sequência.

Os cinco números ficam visíveis desde o primeiro quadro; o nome de cada posição só aparece quando aquele vídeo começa e permanece na lista até o fim. A posição em exibição fica destacada em dourado. A ordem é configurável: `1 → 5` ou contagem regressiva `5 → 1`.

Cada trecho aceita início e duração opcionais; sem isso, o vídeo entra inteiro. Fontes horizontais, verticais e quadradas se misturam sem problema: o enquadramento é *vídeo inteiro com fundo desfocado* ou *preencher a tela com recorte central*. Fonte sem áudio recebe uma trilha silenciosa para que a junção não desalinhe o som das outras.

Esse modo **não transcreve nem chama IA paga** — é download, corte e montagem. A fila reconhece o projeto como etapa única (`top5`), sem passar pelas etapas de transcrição e análise. O ZIP de saída inclui o MP4, a capa, o manifesto e o arquivo de créditos com os cinco links de origem.

Limites: 10 minutos por fonte, 15 minutos no resultado, 500 MB por download. A opacidade das faixas de texto está em `PANEL_ALPHA` (`clipforge/topfive.py`). Use vídeos próprios ou autorizados — a montagem não torna conteúdo reutilizado automaticamente original.

### Limites do enquadramento e da IA

- **Rosto suave e centralizado** prefere manter a câmera no centro, trava o recorte quando o rosto está estável e usa uma trajetória de 30 pontos por segundo com limite de velocidade quando precisa acompanhar movimento. A preferência pelo centro só vale enquanto o rosto continua dentro da área segura do recorte (`CLIPFORGE_SAFE_AREA`, metade central por padrão): quem fala encostado na lateral do quadro é enquadrado onde está. Ajuste fino por `CLIPFORGE_CENTER_BIAS`, `CLIPFORGE_STATIONARY_THRESHOLD`, `CLIPFORGE_MAX_PAN_SPEED`, `CLIPFORGE_HOLD_SECONDS` e `CLIPFORGE_MOTION_FPS`.
- A detecção usa cascatas frontais e de perfil nos dois sentidos, descarta caixas pequenas demais para serem o assunto e segura o enquadramento quando o rosto some por mais de um segundo. Para material difícil (luz baixa, rosto de lado, plano aberto), aponte `CLIPFORGE_FACE_MODEL` para um `face_detection_yunet*.onnx` do OpenCV Zoo: a detecção passa a ser por rede neural e as cascatas viram só a rede de segurança.
- A câmera prefere um movimento único e contínuo a uma sucessão de correções. Quando o assunto atravessa o quadro, a trajetória vira uma reta no tempo (velocidade constante, sem recuos); quando ele fica indo e voltando entre posições recorrentes — balançando na cadeira, por exemplo — o rastreamento para em vez de acompanhar o vaivém. Só volta a perseguir quando as posições estão longe demais para caberem no mesmo recorte. Ajuste por `CLIPFORGE_LINEAR_TOLERANCE`.
- Trecho sem nenhum rosto (gameplay, slide, tela compartilhada, plano aberto) não é mais recortado no centro às cegas: o recorte segue a faixa vertical com mais movimento e detalhe. O clipe avisa quando isso acontece, e `CLIPFORGE_MIN_FACE_COVERAGE` define com que frequência o rosto precisa aparecer para o rastreamento ser considerado confiável.
- **Estimar quem fala** combina atividade labial com presença de áudio e uma espera antes de trocar de participante. É experimental; não é identificação garantida de locutor. Revise podcasts com vários participantes ou prefira tela dividida.
- A prévia do editor aproxima enquadramento e texto. Acompanhe o resultado renderizado para avaliar rastreamento, legenda e áudio finais.
- Correções manuais invalidam a avaliação editorial anterior; a interface sinaliza isso.
- Clipes de origem com baixa resolução são ampliados; exportar em 1080×1920 não recupera detalhes ausentes.
- As legendas preservam os tempos originais ao corrigir palavras. Correções com muitas palavras em um único campo podem exigir ajustar o trecho.

### Análise contextual e aprendizado

O título, a descrição, o canal e as tags fornecidos pelo YouTube entram como contexto e glossário para nomes próprios. Correspondências próximas e de baixa confiança são corrigidas e registradas no cache. Cada frase registra pausas, perguntas, energia vocal, tom, ênfase, mudança de assunto e, quando disponível, o falante. Vídeos longos são resumidos em blocos numa única chamada; uma segunda chamada escolhe candidatos apenas nos melhores blocos, incluindo frases vizinhas. A revisão final pode estender ou encurtar o intervalo por frases completas e rejeita cortes dependentes de contexto ou sem conclusão. Depois do ajuste, sobreposição e diversidade são validadas novamente.

A nota separa gancho, clareza, emoção ou utilidade, densidade, potencial de título, retenção, conclusão e penalidades de repetição, introdução e publicidade. A seleção final reduz assuntos e formatos repetidos. `analysis.json` guarda os candidatos com uma assinatura do vídeo, modelo, público e perfil aprendido, evitando repetir chamadas iguais.

Após cinco clipes com métricas, o worker cria um perfil agregado das melhores durações, formatos e critérios. A avaliação combina retenção e visualizações por dia, compara bons e maus resultados, reduz ajustes quando a amostra é pequena e publica sua confiança no manifesto. O perfil influencia somente os pesos dentro de limites conservadores.

A separação local de falantes usa energia, tom, cruzamentos de zero e assinatura espectral, aceitando uma divisão somente quando os grupos acústicos têm separação forte. `pyannote/speaker-diarization-3.1` oferece a opção de maior precisão: instale `pyannote.audio`, aceite os termos do modelo no Hugging Face e defina `HF_TOKEN`. `CLIPFORGE_DIARIZATION=0` desativa ambas.

Parâmetros: `CLIPFORGE_ANALYSIS_BLOCK_SECONDS` (padrão 240), `CLIPFORGE_CONTEXT_SENTENCES` (2), `CLIPFORGE_SHORTLIST_MULTIPLIER` (3), `CLIPFORGE_TRIAGE_MODEL` e `CLIPFORGE_REVIEW_MODEL`. O modelo de triagem resume blocos e o de revisão faz a auditoria editorial final. A transcrição e o índice visual global rodam simultaneamente; depois, rosto e gancho audiovisual dos candidatos também são analisados em paralelo. FFmpeg continua renderizando os arquivos de forma controlada para evitar saturar a máquina.

O gancho final combina texto com silêncio inicial, energia de áudio e mudança visual observada nos primeiros dois segundos.

O índice multimodal preserva identidades faciais aproximadas, expressões de sorriso, cenas, energia, tom, falantes, texto falado, OCR e objetos quando um modelo OpenCV DNN é configurado. O modo de locutor ativo cruza mudança de falante e movimento labial e só troca o enquadramento em uma fronteira natural. A legenda muda para o topo quando o OCR encontra texto predominante na parte inferior.

A edição automática remove pausas acima de `CLIPFORGE_INTERNAL_SILENCE_SECONDS` e vícios isolados cercados por pausa. Correções textuais passam por alinhamento local às transições de energia do áudio, e o destaque karaokê é subdividido em grupos fonéticos. Um zoom curto disfarça os pontos de edição.

### Onde o tempo é gasto

Medido num vídeo 1080p de 231 s, sem GPU, em oito núcleos. As frações são do tempo de vídeo: `0,25x` quer dizer quinze segundos de trabalho por minuto de vídeo.

| etapa | tempo | fração |
| --- | --- | --- |
| envelope de áudio (garimpo) | 0,1 s | 0,00x |
| índice visual (cenas, 0,5 fps) | 5,8 s | 0,03x |
| transcrição (depois do ajuste abaixo) | 44 s | 0,19x |
| índice de mídia (rostos, 1 Hz) | 57,5 s | 0,25x |
| **renderização, por clipe** | **82 s** (clipe de 45 s) | **1,8x** |

A renderização domina, porque é a única etapa que se multiplica pelo número de clipes: dez clipes custam dez renderizações, enquanto as etapas de análise rodam uma vez só. Cada clipe passa por **duas** codificações — a base (recorte, câmera, cortes internos) e depois a queima da legenda. É deliberado: a base fica em cache, então corrigir uma palavra ou mudar o estilo da legenda re-renderiza só a segunda passagem, que é muito mais barata que refazer tudo. O preço é a primeira renderização sair mais cara.

Num projeto de dez clipes isso dá cerca de catorze minutos só de renderização, contra sete minutos de análise — o que bate com os tempos observados em vídeos reais. Baixar o preset rende pouco: `veryfast` com CRF 21 terminou em 69 s contra 82 s do `medium` com CRF 19, porque a segunda passagem e as capas pesam tanto quanto a codificação da base. Quem tem GPU ganha muito mais com `CLIPFORGE_VIDEO_ENCODER=nvenc` do que mexendo em `CLIPFORGE_PRESET` e `CLIPFORGE_CRF`.

### Desempenho da transcrição

Sem GPU, a transcrição é o trecho mais longo do processamento, e dois parâmetros mudam bastante o tempo. Medido em 60 s de fala em português, modelo `small` em `int8`, oito núcleos:

| configuração | tempo | velocidade |
| --- | --- | --- |
| feixe 5, threads da biblioteca (padrão antigo) | 17,1 s | 3,5x |
| feixe 5, 8 threads | 15,2 s | 4,0x |
| feixe 1, 8 threads | 12,3 s | 4,9x |
| **feixe 2, 8 threads (padrão atual)** | **11,4 s** | **5,3x** |

As cinco configurações devolveram o **mesmo texto, palavra por palavra** — o feixe 5 custava um terço a mais de tempo sem mudar nada. Em áudio ruidoso a diferença pode aparecer, por isso `CLIPFORGE_WHISPER_BEAM` continua ajustável (e volta a 5 automaticamente quando há GPU, onde a busca em feixe é barata).

## Confiabilidade

Uploads ficam em estado `uploading` e em arquivo `.part` até a cópia terminar. Só depois entram na fila. O limite padrão é 4 GB, configurável por `CLIPFORGE_MAX_UPLOAD_BYTES`.

As filas usam transações SQLite. Workers renovam uma concessão de execução por heartbeat; tarefas abandonadas são recuperadas. Tokens impedem workers antigos de concluir uma tarefa reassumida por outro processo. Apenas uma edição por projeto é processada de cada vez.

Novos projetos possuem tarefas independentes de transcrição, análise, rastreamento e renderização em `pipeline_stages`. Cada tarefa tem concessão e heartbeat próprios; vários workers podem processar projetos diferentes sem repetir uma etapa concluída. Os artefatos persistentes são `transcript.json`, `analysis.json`, `visual-index.json`, `media-index.json` e as bases incrementais em `cache/`.

A renderização incremental guarda o vídeo vertical com câmera e áudio tratados, sem legenda queimada, além das três capas e da trajetória. Uma alteração apenas textual reaplica as legendas e copia o áudio processado. Mudanças no intervalo, layout ou tratamento de áudio criam outra chave de cache.

O cache da transcrição verifica arquivo, tamanho, data de alteração, modelo e idioma. O modelo Whisper permanece carregado no worker. Erros de IA geram fallback explicitamente identificado, com o provedor efetivamente usado no manifesto.

## Testes

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

A suíte ativa está em `tests/test_api.py`, `tests/test_engine.py` e `tests/test_topfive.py`. Gera mídia sintética e usa FFmpeg real, sem downloads de modelos ou chamadas pagas. Cobre upload incompleto, limites, caminhos no Windows, concorrência, versões, publicação, ZIP, CSV, cache, legendas, os cinco layouts, o garimpo de transmissões longas e a montagem do Top 5 (validação de link, fila de etapa única, revelação do ranking e montagem real com fontes de formatos diferentes). `tests/test_pipeline.py` mantém o verificador legado executável manualmente.

O teste opcional de navegador usa Playwright: `tests/serve_ui.py` inicia uma base isolada, `tests/serve_ui.py --worker` processa suas edições e `node tests/ui_smoke.cjs` percorre revisão, publicação e métricas. Consulte os caminhos de fixture no script para reproduzir. As capturas ficam em `tests/artifacts`.

## CLI

```bash
python -m clipforge.run entrevista.mp4 -o ./saida -n 5 --min 20 --max 60 --niche tecnologia --audience iniciantes
python -m clipforge.run entrevista.mp4 -o ./saida --provider heuristic --layout fit --denoise
```

## Estrutura

- `clipforge/`: seleção, transcrição, legendas, enquadramento e renderização.
- `clipforge/editor.py`: renderização de uma versão e gravação atômica de JSON.
- `clipforge/topfive.py`, `api/topfive.py`, `web/topfive.js`: montagem do Top 5 de TikTok.
- `clipforge/prospect.py`: garimpo de momentos em transmissões longas.
- `api/main.py`: uploads, projetos, arquivos e pacotes.
- `api/studio.py`: revisão, publicação e métricas.
- `api/db.py`: banco, filas, versões e snapshots.
- `api/worker.py`: processamento de projetos e edições.
- `web/index.html`, `web/studio.js`, `web/studio.css`: interface.
- `scripts/start.ps1`: execução local no Windows.

## Conteúdo e monetização

A aba **Análise do canal** aceita o link HTTPS de um canal (`/@nome` ou `/channel/ID`) e consulta até 20 vídeos públicos, separando vídeos e Shorts. Mostra títulos, durações e visualizações disponíveis; dados ausentes permanecem desconhecidos. O YouTube pode limitar a consulta. Essa amostra não representa o histórico completo do canal.

A aba agora prioriza **Conectar YouTube**, sem digitação de métricas. Após a configuração inicial, os dados oficiais do canal autorizado são carregados ao abrir a aba e atualizados a cada cinco minutos enquanto ela estiver visível. Há períodos de 7, 28 e 90 dias, comparação com o período anterior, visualizações, horas assistidas, porcentagem média assistida, saldo de inscritos, origens de tráfego e os dez vídeos mais assistidos. O painel reúne todos os formatos e não mede penalizações nem garante recomendações.

### Conectar a conta Google (Windows)

1. No Google Cloud, ative **YouTube Data API v3**, **YouTube Analytics API** e **YouTube Reporting API**.
2. Configure o consentimento OAuth; em modo de teste, adicione sua conta como usuário de teste.
3. Crie um cliente OAuth **Aplicativo para computador**, baixe o JSON e importe na seção de configuração da aba. Também é possível usar um cliente Web, cadastrando exatamente o endereço de retorno exibido pela aplicação (ex.: `http://127.0.0.1:8001/api/youtube/callback`).
4. Clique em **Conectar YouTube**. O aplicativo abre o navegador padrão para o login Google; selecione a conta proprietária do canal e autorize os dois escopos de leitura. A aba integrada detecta a conexão. Não há permissão de upload ou alteração de vídeos.

Opcionalmente configure `YOUTUBE_CLIENT_ID` e `YOUTUBE_CLIENT_SECRET` no `.env` e reinicie a API. Essas variáveis têm precedência sobre o arquivo importado. Aplicativos Google em teste podem exigir reconexão após sete dias. Credenciais e refresh tokens são criptografados usando Windows DPAPI em `storage/youtube-credentials.bin`, fora dos arquivos públicos. Não copie esse arquivo para outro usuário do Windows. **Desconectar** revoga o token Google e remove os dados locais da conexão. Os logs de acesso estão desativados no lançador para não registrar códigos OAuth.

Impressões e CTR de miniaturas vêm do relatório oficial `channel_reach_basic_a1`, criado automaticamente quando necessário. Os primeiros relatórios podem levar até 48 horas. O painel identifica dias ainda indisponíveis, mostra a cobertura parcial e só compara alcance quando ambos os períodos estão completos. O CTR agregado é ponderado pelas impressões; relatórios revisados substituem os anteriores sem duplicar contagens. A API pode atrasar ou revisar dados recentes e não representa todas as métricas do Studio, como todas as exibições do feed de Shorts. A coleta depende da aplicação aberta; não existe agendamento em segundo plano quando ela está fechada.

Referências: [OAuth para aplicativos locais](https://developers.google.com/identity/protocols/oauth2/native-app), [relatórios de alcance](https://developers.google.com/youtube/reporting/v1/reports/channel_reports#reach-reports) e [geração de relatórios](https://developers.google.com/youtube/reporting/v1/reports).

Testes da integração (sem acesso real ao Google): `.venv\Scripts\python.exe -m pytest tests/test_channel.py tests/test_youtube.py -q`. Cobrem configuração, proteção de origem, PKCE, callback, renovação, revogação, ponderação de CTR, cache e relatórios indisponíveis.

As instruções de seleção por IA priorizam contexto preservado, títulos fiéis, exemplos próprios e diversidade editorial. Isso não certifica originalidade: revise o material e seus direitos antes de publicar. Revise títulos, contexto e contribuição própria antes de publicar.

O sistema auxilia a produção; monetização depende do conteúdo, do canal e das políticas do YouTube. Legendas e cortes por si só não garantem elegibilidade de material reutilizado. Veja as [políticas oficiais](https://support.google.com/youtube/answer/1311392?hl=pt-BR).
