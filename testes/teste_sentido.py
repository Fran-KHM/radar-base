"""Teste do sentido.py com uma avenida de exemplo (roda na rotina, antes de gerar a base).

Cenario: avenida com canteiro no eixo lon -46.7001; pista norte (mao unica, sentido 0 grau) em lon -46.70000 e
pista sul (mao unica, 180 graus) em lon -46.70020, ~20 m entre elas. Uma rua de mao dupla em lon -46.69.
  radar A colado na pista norte (4 m)      -> sentido 0
  radar B colado na pista sul (4 m)        -> sentido 180
  radar C no meio do canteiro (10 m de cada) -> sem sentido (incerto)
  radar D na rua de mao dupla              -> sem sentido
  radar E (CET) colado na pista norte      -> sentido 0
  radar F (CET desativado)                 -> ignorado
"""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sentido  # noqa: E402

pasta = tempfile.mkdtemp()
radares = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "id": "nA", "geometry": {"type": "Point", "coordinates": [-46.70004, -23.5000]}},
    {"type": "Feature", "id": "nB", "geometry": {"type": "Point", "coordinates": [-46.70016, -23.5003]}},
    {"type": "Feature", "id": "nC", "geometry": {"type": "Point", "coordinates": [-46.70010, -23.5006]}},
    {"type": "Feature", "id": "nD", "geometry": {"type": "Point", "coordinates": [-46.69002, -23.5000]}},
    # R: radar 110 m depois de uma esquina (ruas de chegada, 07/11)
    {"type": "Feature", "id": "nR", "geometry": {"type": "Point", "coordinates": [-46.68004, -23.4910]}},
]}
with open(os.path.join(pasta, "r.geojson"), "w") as f:
    json.dump(radares, f)
with open(os.path.join(pasta, "c.csv"), "w") as f:
    f.write("CODIGO,ATIVACAO,DESATIVACAO,SERIE,DESCRICAO,LATITUDE,LONGITUDE\n")
    f.write("1,2024-01-01,,1,E,-23.4995,-46.70003\n")
    f.write("2,2020-01-01,2023-01-01,2,F,-23.4990,-46.70003\n")
    # sentido declarado (06/11): a avenida comeca na ponta sul (mais perto do Marco Zero); CENTRO/BAIRRO = norte
    f.write('3,2024-01-01,,3,"AV TESTE (CENTRO/BAIRRO) NR 100",-23.5009,-46.70010\n')        # G: no canteiro -> 0
    f.write('4,2024-01-01,,4,"AV. TESTE C/B, NR 200",-23.5012,-46.70017\n')                  # H: colado na pista sul -> 0
    f.write('5,2024-01-01,,5,"AV TESTE B/C, NR 201/AV TESTE C/B, NR 200",-23.5015,-46.70010\n')  # I: as duas maos -> sem
    f.write('6,2024-01-01,,6,"AV TESTE (INTERLAGOS/SANTANA)",-23.5018,-46.70017\n')         # J: Interlagos e ao sul -> 0
    f.write('7,2024-01-01,,7,"AV TESTE (BAIRRO/CENTRO)",-23.4985,-46.70003\n')              # K: colado na norte -> 180
vias = [
    {"type": "Feature", "id": "w1", "geometry": {"type": "LineString", "coordinates": [[-46.70000, -23.502], [-46.70000, -23.498]]}, "properties": {"highway": "primary", "oneway": "yes", "name": "Avenida Teste"}},
    {"type": "Feature", "id": "w2", "geometry": {"type": "LineString", "coordinates": [[-46.70020, -23.498], [-46.70020, -23.502]]}, "properties": {"highway": "primary", "oneway": "yes", "name": "Avenida Teste"}},
    {"type": "Feature", "id": "w3", "geometry": {"type": "LineString", "coordinates": [[-46.69000, -23.502], [-46.69000, -23.498]]}, "properties": {"highway": "residential"}},
    # esquina em (-23.4900, -46.6800): B desce para o sul (mao unica) com o radar R; A chega do oeste (mao unica para
    # leste), D chega do norte (mao dupla), C sai para o leste (mao unica, nao leva ao radar)
    {"type": "Feature", "id": "wB", "geometry": {"type": "LineString", "coordinates": [[-46.6800, -23.4900], [-46.6800, -23.4940]]}, "properties": {"highway": "secondary", "oneway": "yes"}},
    {"type": "Feature", "id": "wA", "geometry": {"type": "LineString", "coordinates": [[-46.6830, -23.4900], [-46.6800, -23.4900]]}, "properties": {"highway": "secondary", "oneway": "yes"}},
    {"type": "Feature", "id": "wC", "geometry": {"type": "LineString", "coordinates": [[-46.6800, -23.4900], [-46.6770, -23.4900]]}, "properties": {"highway": "secondary", "oneway": "yes"}},
    {"type": "Feature", "id": "wD", "geometry": {"type": "LineString", "coordinates": [[-46.6800, -23.4880], [-46.6800, -23.4900]]}, "properties": {"highway": "residential"}},
]
with open(os.path.join(pasta, "v.geojsonseq"), "w") as f:
    for v in vias:
        f.write(json.dumps(v) + "\n")

saida = os.path.join(pasta, "s.json")
sentido.arq_pistas = os.path.join(pasta, "p.json")
sentido.arq_chegadas = os.path.join(pasta, "c.json")
sentido.main(os.path.join(pasta, "r.geojson"), os.path.join(pasta, "c.csv"), os.path.join(pasta, "v.geojsonseq"), saida)
with open(saida) as f:
    s = json.load(f)

esperado = {
    sentido.chave(-23.5000, -46.70004): 0,
    sentido.chave(-23.5003, -46.70016): 180,
    sentido.chave(-23.4995, -46.70003): 0,
    sentido.chave(-23.5009, -46.70010): 0,     # G: CENTRO/BAIRRO no canteiro
    sentido.chave(-23.5012, -46.70017): 0,     # H: C/B colado na pista sul (a posicao nao decide mais)
    sentido.chave(-23.5018, -46.70017): 0,     # J: de Interlagos (sul) para Santana
    sentido.chave(-23.4985, -46.70003): 180,   # K: BAIRRO/CENTRO colado na pista norte
}
if sentido.chave(-23.5015, -46.70010) in s:
    erros_i = [f"I (as duas maos) nao devia ter sentido, veio {s[sentido.chave(-23.5015, -46.70010)]}"]
else:
    erros_i = []
d = sentido.ler_descricao("AL DOS TUPINIQUINS X AV DOS BANDEIRANTES (IMIGRAN/M PINHEIROS)")
if d != ([frozenset({"TUPINIQUINS"}), frozenset({"BANDEIRANTES"})], ("para", "IMIGRAN", "M PINHEIROS")):
    erros_i.append(f"ler_descricao com cruzamento: {d}")
if sentido.ler_descricao("AV DEP CANTIDIO SAMPAIO, BAIRRO/CENTRO X R JOSE EDUARDO PEREIRA II")[1] != "bc":
    erros_i.append("ler_descricao BAIRRO/CENTRO sem parenteses")
if sentido.ler_descricao("AV SANTA INES C/B, NR 3200") != ([frozenset({"INES"})], "cb"):
    erros_i.append(f"ler_descricao C/B: {sentido.ler_descricao('AV SANTA INES C/B, NR 3200')}")
erros = list(erros_i)
for k, v in esperado.items():
    if s.get(k) != v:
        erros.append(f"{k}: esperado {v}, veio {s.get(k)}")
for k in (sentido.chave(-23.5006, -46.70010), sentido.chave(-23.5000, -46.69002), sentido.chave(-23.4990, -46.70003)):
    if k in s:
        erros.append(f"{k}: nao devia ter sentido, veio {s[k]}")
# tracado da pista: A (pista norte) e D (rua de mao dupla) tem; C (no canteiro, incerto) nao tem.
# A pista norte e uma reta a 0,00004 grau a leste do radar A (dlon = 4) e vai de -23.502 a -23.498, menos que os
# 400 m de alcance: o tracado para nas pontas da via (dlat = -200 e +200)
with open(sentido.arq_pistas) as f:
    pistas = json.load(f)
pa = pistas.get(sentido.chave(-23.5000, -46.70004))
if not pa or len(pa) != 4:
    erros.append(f"pista do radar A devia ser uma reta (2 pontos), veio {pa}")
else:
    dlat = [pa[0], pa[2]]
    if not (min(dlat) == -200 and max(dlat) == 200 and pa[1] == pa[3] == 4):
        erros.append(f"pista do radar A fora do esperado: {pa}")
if sentido.chave(-23.5000, -46.69002) not in pistas:
    erros.append("rua de mao dupla (D) devia ter tracado")
if sentido.chave(-23.5006, -46.70010) in pistas:
    erros.append("radar no canteiro (C) nao devia ter tracado")

# H declarou C/B (norte) mas esta colado na pista sul: o tracado tem de ser o da pista norte (dlon = +17)
ph = pistas.get(sentido.chave(-23.5012, -46.70017))
if not ph or ph[1] != 17:
    erros.append(f"pista do radar H devia ser a norte (dlon 17), veio {ph}")
if sentido.chave(-23.5015, -46.70010) in pistas:
    erros.append("radar I (as duas maos, avenida com canteiro) nao devia ter tracado")

# Ruas de chegada do radar R (110 m depois da esquina): cobre A (80 m a oeste da esquina) e D (55 m ao norte), nao cobre C (sai da
# esquina), e cada caminho anda no sentido do carro (o ultimo ponto e o mais perto do radar)
with open(sentido.arq_chegadas) as f:
    chegadas = json.load(f)
cr = chegadas.get(sentido.chave(-23.4910, -46.68004))


def perto_de_chegada(lat, lon, rlat=-23.4910, rlon=-46.68004):
    cl = math.cos(math.radians(rlat))
    px, py = (lon - rlon) * 111195 * cl, (lat - rlat) * 111195
    melhor = 1e9
    for cad in cr or []:
        pts = [(cad[i + 1] / 100000 * 111195 * cl, cad[i] / 100000 * 111195) for i in range(0, len(cad), 2)]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
            melhor = min(melhor, math.hypot(ax + t * dx - px, ay + t * dy - py))
    return melhor


if s.get(sentido.chave(-23.4910, -46.68004)) != 180:
    erros.append(f"R devia ter sentido 180, veio {s.get(sentido.chave(-23.4910, -46.68004))}")
if not cr:
    erros.append("R devia ter ruas de chegada")
else:
    if perto_de_chegada(-23.4900, -46.6808) > 8:
        erros.append(f"chegada de R devia cobrir a rua A: {cr}")
    if perto_de_chegada(-23.4895, -46.6800) > 8:
        erros.append(f"chegada de R devia cobrir a rua D: {cr}")
    if perto_de_chegada(-23.4900, -46.6785) < 30:
        erros.append(f"chegada de R nao devia cobrir a rua C: {cr}")
    if perto_de_chegada(-23.4900, -46.6829) < 30:
        erros.append(f"chegada de R devia parar em ~220 m de caminho: {cr}")
    for cad in cr:
        d_ini = math.hypot(cad[0], cad[1])
        d_fim = math.hypot(cad[-2], cad[-1])
        if d_fim > d_ini:
            erros.append(f"caminho de chegada fora da ordem do carro: {cad}")

if erros:
    print("FALHOU:\n  " + "\n  ".join(erros))
    sys.exit(1)
print("teste_sentido: ok (25 casos)")
