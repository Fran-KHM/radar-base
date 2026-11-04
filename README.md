# radar-base

Base de radares usada pelo app **Radar** (Garmin Connect IQ, uso pessoal). O relógio baixa pelo celular só os blocos da região onde está.

- `blocos/t<chave>.json`: radares de um bloco de 0,1 grau (~11 km), no formato `{"r": [...]}`. Chave = `floor((lat + 90) / 0,1) * 10000 + floor((lon + 180) / 0,1)`. Cada radar ocupa 5 números: latitude × 100000, longitude × 100000, limite em km/h (0 = sem), direção fiscalizada em graus (-1 = sem) e tipo em bits (1 velocidade, 2 semáforo, 4 lombada eletrônica, 8 faixa de ônibus).
- `blocos/indice.json`: data, contagens e lista de chaves.
- `sentido.py`: descobre o sentido fiscalizado de cada radar pela pista de mão única mais próxima (avenidas com canteiro, marginais, rodovias), para o app ignorar o radar da outra mão. Só grava quando não há outra via quase tão perto. Teste em `testes/teste_sentido.py`.
- `gerar_base.ps1`: monta tudo. Roda todo dia às 03:17 (Brasília) pelo GitHub Actions (`.github/workflows/atualizar.yml`).

## Fontes e licenças

- Capital de SP: [CET, Fiscalização Eletrônica](https://github.com/cetsp-dados/dados-fiscalizacao-eletronica), licença CC-BY 4.0. Só locais ativos.
- Demais locais: radares (`highway=speed_camera`) do OpenStreetMap, © colaboradores do OpenStreetMap, licença [ODbL](https://www.openstreetmap.org/copyright). Esta base derivada fica sob a mesma licença.

Aviso por GPS a partir de uma lista de locais, sem detectar sinal de radar.
