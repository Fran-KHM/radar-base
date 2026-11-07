# Monta a base de radares do app Radar (Garmin): CET (capital de SP) + OpenStreetMap, dividida em blocos de Tam graus.
# Roda no Windows PowerShell 5.1 e no PowerShell 7 do Linux (GitHub Actions).
#
#   -Saida servidor : grava <Destino>/blocos/t<chave>.json e blocos/indice.json (o relogio baixa pelo celular)
#   -Saida app      : grava a base embutida no projeto do app (<Destino>/resources/jsondata e source/Tiles.mc)
#
# Uso: pwsh ./gerar_base.ps1 -Saida servidor -Destino . [-Area BR]
#      powershell -File gerar_base.ps1 -Saida app -Destino <pasta do app> -Area BR-SP
#
# Dados: CET (CC-BY 4.0, github.com/cetsp-dados/dados-fiscalizacao-eletronica) e
#        (c) colaboradores do OpenStreetMap (ODbL, openstreetmap.org/copyright).
param(
    [ValidateSet('servidor', 'app')][string]$Saida = 'servidor',
    [string]$Destino = '.',
    [string]$Area = 'BR',          # BR (pais) ou BR-SP, BR-RJ...
    [double]$Tam = 0.1,            # bloco em graus (~11 km)
    [string]$Trabalho = '',        # pasta das copias baixadas (padrao: <Destino>/dados)
    [string]$OsmGeoJson = '',      # radares ja filtrados do extrato do Geofabrik (osmium export); vazio = Overpass
    [string]$Sentidos = '',        # sentidos.json do sentido.py: sentido de cada radar pela pista de mao unica
    [string]$Pistas = '',          # pistas.json do sentido.py: tracado da pista de cada radar (+-400 m)
    [string]$Chegadas = ''         # chegadas.json do sentido.py: ruas que levam a cada radar (arquivo c<bloco>.json a parte)
)
$ErrorActionPreference = 'Stop'
$inv = [Globalization.CultureInfo]::InvariantCulture
$Destino = (Resolve-Path $Destino).Path
if (-not $Trabalho) { $Trabalho = Join-Path $Destino 'dados' }
$ua = 'radar-base/1.0 (github.com/Fran-KHM/radar-base)'

function Pasta([string]$p) { New-Item -ItemType Directory -Force $p | Out-Null; $p }
function Num([string]$s) { if ($s -match '^\s*(\d+)') { return [int]$Matches[1] } return 0 }

# 1. OpenStreetMap. Na rotina do GitHub vem do extrato diario do Brasil no Geofabrik, ja filtrado pelo osmium
#    (confiavel e feito para download). Sem -OsmGeoJson, consulta o Overpass estado por estado (uso local);
#    se um estado falhar, usa a ultima copia boa dele; se nunca houve copia, para com erro.
$todos = New-Object System.Collections.ArrayList
$vistos = @{}
$ufs = @()
if ($OsmGeoJson) {
    foreach ($f in (Get-Content $OsmGeoJson -Raw | ConvertFrom-Json).features) {
        if ($f.geometry.type -ne 'Point') { continue }
        $id = "$($f.id)"
        if ($vistos.ContainsKey($id)) { continue }
        $vistos[$id] = $true
        [void]$todos.Add([pscustomobject]@{ id = $id; lat = [double]$f.geometry.coordinates[1]; lon = [double]$f.geometry.coordinates[0]; tags = $f.properties })
    }
    "Geofabrik: $($todos.Count) radares"
} elseif ($Area -eq 'BR') {
    $ufs = 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.Split(' ') | ForEach-Object { "BR-$_" }
} else {
    $ufs = @($Area)
}
$dirUf = Pasta (Join-Path $Trabalho 'uf')
foreach ($uf in $ufs) {
    $arqUf = Join-Path $dirUf "$uf.json"
    $q = "[out:json][timeout:180];area[`"ISO3166-2`"=`"$uf`"][admin_level=4]->.a;node[`"highway`"=`"speed_camera`"](area.a);out;"
    $res = $null
    for ($t = 0; $t -lt 3 -and -not $res; $t++) {
        foreach ($srv in 'https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter') {
            try { $res = Invoke-RestMethod -Method Post -Uri $srv -Body @{ data = $q } -TimeoutSec 200 -UserAgent $ua; break }
            catch { Write-Warning "$uf em $srv falhou: $($_.Exception.Message)" }
        }
        if (-not $res) { Start-Sleep -Seconds (20 * ($t + 1)) }
    }
    if ($res) {
        $res | ConvertTo-Json -Depth 5 -Compress | Set-Content -Encoding utf8 $arqUf
        $origem = 'novo'
    } elseif (Test-Path $arqUf) {
        $res = Get-Content $arqUf -Raw | ConvertFrom-Json
        $origem = 'copia anterior'
    } else {
        throw "Nenhum servidor Overpass respondeu para $uf e nao ha copia anterior."
    }
    $n = 0
    foreach ($e in $res.elements) { if (-not $vistos.ContainsKey($e.id)) { $vistos[$e.id] = $true; [void]$todos.Add($e); $n++ } }
    "$uf : $n radares ($origem)"
}

# Tipo do radar (bits): 1 = velocidade, 2 = semaforo, 4 = lombada eletronica, 8 = faixa de onibus
$pts = New-Object System.Collections.ArrayList

# 2. CET (capital): base oficial. So locais ativos. Codigos de ENQUADRAMENTO pela aba "Dominio - Enquadramento"
#    de Dicionario_Dados_Locais_Fiscalizados.xlsx. VELOCIDADE "60/50 km/h": o 1o numero e o de carros.
$dirCet = Pasta (Join-Path $Trabalho 'cet')
$arqCet = Join-Path $dirCet 'Base_Locais_Fiscalizados.csv'
try { Invoke-WebRequest 'https://raw.githubusercontent.com/cetsp-dados/dados-fiscalizacao-eletronica/HEAD/Base_Locais_Fiscalizados.csv' -OutFile $arqCet -UseBasicParsing -UserAgent $ua -TimeoutSec 60 }
catch { Write-Warning "CET indisponivel, usando a copia anterior: $($_.Exception.Message)" }
$grade = @{}   # celulas de 0,01 grau com os pontos da CET, para tirar duplicados do OSM
$nCet = 0; $nSemaf = 0; $nLomb = 0; $nOnibus = 0
if (Test-Path $arqCet) {
    # cabecalhos proprios em ASCII: no PowerShell 5.1 este arquivo e lido como ANSI e os nomes acentuados nao bateriam
    $cab = 'CODIGO', 'ATIVACAO', 'DESATIVACAO', 'SERIE', 'DESCRICAO', 'LATITUDE', 'LONGITUDE', 'FAIXAS', 'TIPO', 'METROLOGICO', 'VELOCIDADE', 'FAIXA_AZUL', 'CAPTURA', 'REDUTOR', 'ENQUADRAMENTO'
    foreach ($c in (Import-Csv $arqCet -Encoding UTF8 -Header $cab | Select-Object -Skip 1)) {
        $lat = [double]::Parse($c.LATITUDE, $inv)
        $lon = [double]::Parse($c.LONGITUDE, $inv)
        # todo local da CET, ativo ou desativado, tira o ponto do OSM no mesmo lugar (o OSM tem radares ja removidos)
        $g = "$([Math]::Floor($lat * 100)),$([Math]::Floor($lon * 100))"
        if (-not $grade.ContainsKey($g)) { $grade[$g] = New-Object System.Collections.ArrayList }
        [void]$grade[$g].Add(@($lat, $lon))
        if ($c.DESATIVACAO) { continue }
        $enq = @($c.ENQUADRAMENTO -split ';' | ForEach-Object { $_.Trim() })
        $tipo = 0
        if ($enq -contains 'V' -or $c.METROLOGICO -eq 'SIM') { $tipo += 1 }
        if ($enq -contains 'A' -or $enq -contains 'P') { $tipo += 2 }   # avanco do vermelho ou parada sobre a faixa de pedestres
        if ($c.REDUTOR -match '^S') { $tipo += 4 }                      # lombada eletronica (redutor), sempre com velocidade
        if ($enq -contains 'EXE' -or $enq -contains 'EXD') { $tipo += 8 }   # faixa exclusiva de onibus (esquerda/direita)
        # fora (pedido do Francisco ou nao vale para carro): R (rodizio), Z (ZMRC caminhao), F (ZMRF fretado),
        # PR (faixa proibida caminhao), VR (VER caminhao), MT (moto), S (transito seletivo), CPD/CPE (conversao proibida)
        if ($tipo -eq 0) { continue }
        $vel = if ($tipo -band 1) { Num $c.VELOCIDADE } else { 0 }
        [void]$pts.Add(@($lat, $lon, $vel, -1, $tipo))
        $nCet++; if ($tipo -band 2) { $nSemaf++ }; if ($tipo -band 4) { $nLomb++ }; if ($tipo -band 8) { $nOnibus++ }
    }
}

# 3. OpenStreetMap: tudo o que nao for o mesmo ponto de um radar da CET (ate 150 m)
function PertoDaCet([double]$lat, [double]$lon) {
    $gi = [Math]::Floor($lat * 100); $gj = [Math]::Floor($lon * 100)
    for ($a = -1; $a -le 1; $a++) { for ($b = -1; $b -le 1; $b++) {
        $l = $grade["$($gi + $a),$($gj + $b)"]
        if ($l) { foreach ($p in $l) {
            $dy = ($p[0] - $lat) * 111195; $dx = ($p[1] - $lon) * 111195 * 0.917
            if ($dx * $dx + $dy * $dy -lt 22500) { return $true }
        } }
    } }
    return $false
}
$nOsm = 0
foreach ($e in $todos) {
    if (PertoDaCet ([double]$e.lat) ([double]$e.lon)) { continue }
    $dir = -1
    if ($e.tags.direction -match '^\s*(\d+(\.\d+)?)\s*$') { $dir = [int][Math]::Round([double]::Parse($Matches[1], $inv)) % 360 }
    [void]$pts.Add(@([double]$e.lat, [double]$e.lon, (Num $e.tags.maxspeed), $dir, 1))
    $nOsm++
}

# 4. Sentido pela pista de mao unica (sentido.py), so para quem nao tem direcao propria no OSM
$nSentido = 0
if ($Sentidos -and (Test-Path $Sentidos)) {
    $mapa = Get-Content $Sentidos -Raw | ConvertFrom-Json
    foreach ($p in $pts) {
        if ($p[3] -ge 0) { continue }
        $k = "$([Math]::Floor($p[0] * 100000 + 0.5)),$([Math]::Floor($p[1] * 100000 + 0.5))"
        $v = $mapa.$k
        if ($null -ne $v) { $p[3] = [int]$v; $nSentido++ }
    }
}

# Tracado da pista de cada radar (sentido.py); radares sem tracado ficam com lista vazia
$mapaPistas = $null
$nPista = 0
if ($Pistas -and (Test-Path $Pistas)) { $mapaPistas = Get-Content $Pistas -Raw | ConvertFrom-Json }
$mapaCheg = $null
$nCheg = 0
if ($Chegadas -and (Test-Path $Chegadas)) { $mapaCheg = Get-Content $Chegadas -Raw | ConvertFrom-Json }
$chegBloco = @{}

# 5. Blocos: chave = linha * 10000 + coluna; cada radar = lat x 100000, lon x 100000, limite (0 = sem),
#    direcao fiscalizada em graus (-1 = sem), tipo (bits). Em paralelo, "v": o tracado da pista de cada radar,
#    na mesma ordem ([dlat, dlon, ...] x 100000 relativos ao radar; [] = sem tracado)
$blocos = @{}
$viasBloco = @{}
foreach ($p in $pts) {
    $k = [int][Math]::Floor(($p[0] + 90.0) / $Tam) * 10000 + [int][Math]::Floor(($p[1] + 180.0) / $Tam)
    if (-not $blocos.ContainsKey($k)) { $blocos[$k] = New-Object System.Collections.ArrayList; $viasBloco[$k] = New-Object System.Collections.ArrayList; $chegBloco[$k] = New-Object System.Collections.ArrayList }
    [void]$blocos[$k].AddRange(@([int][Math]::Round($p[0] * 100000), [int][Math]::Round($p[1] * 100000), $p[2], $p[3], $p[4]))
    $pista = '[]'
    if ($mapaPistas) {
        $v = $mapaPistas."$([Math]::Floor($p[0] * 100000 + 0.5)),$([Math]::Floor($p[1] * 100000 + 0.5))"
        if ($null -ne $v) { $pista = '[' + ($v -join ',') + ']'; $nPista++ }
    }
    [void]$viasBloco[$k].Add($pista)
    # ruas de chegada: lista de caminhos [[dlat, dlon, ...], ...] na mesma ordem dos radares ([] = sem)
    $cheg = '[]'
    if ($mapaCheg) {
        $c = $mapaCheg."$([Math]::Floor($p[0] * 100000 + 0.5)),$([Math]::Floor($p[1] * 100000 + 0.5))"
        if ($null -ne $c) { $cheg = '[' + ((@($c) | ForEach-Object { '[' + (@($_) -join ',') + ']' }) -join ',') + ']'; $nCheg++ }
    }
    [void]$chegBloco[$k].Add($cheg)
}
$chaves = @($blocos.Keys | Sort-Object)
$tamTxt = $Tam.ToString($inv)
$hoje = (Get-Date).ToString('yyyy-MM-dd')

if ($Saida -eq 'servidor') {
    $dirBlocos = Pasta (Join-Path $Destino 'blocos')
    Get-ChildItem $dirBlocos -Filter 't*.json' | Remove-Item -Confirm:$false
    # objeto {"r":[...]} e nao lista solta: o makeWebRequest do relogio espera um dicionario JSON
    foreach ($k in $chaves) { ('{"r":[' + ($blocos[$k] -join ',') + '],"v":[' + ($viasBloco[$k] -join ',') + ']}') | Set-Content -Encoding ascii (Join-Path $dirBlocos "t$k.json") }
    # ruas de chegada num arquivo a parte por bloco: se o relogio nao conseguir baixar (tamanho), o resto funciona
    Get-ChildItem $dirBlocos -Filter 'c*.json' | Remove-Item -Confirm:$false
    if ($mapaCheg) { foreach ($k in $chaves) { ('{"c":[' + ($chegBloco[$k] -join ',') + ']}') | Set-Content -Encoding ascii (Join-Path $dirBlocos "c$k.json") } }
    $indice = [ordered]@{ atualizado = $hoje; area = $Area; tam = $Tam; total = $pts.Count; cet = $nCet; osm = $nOsm; semaforos = $nSemaf; lombadas = $nLomb; onibus = $nOnibus; com_sentido = $nSentido; com_pista = $nPista; com_chegada = $nCheg; chaves = $chaves }
    $indice | ConvertTo-Json -Compress | Set-Content -Encoding ascii (Join-Path $dirBlocos 'indice.json')
} else {
    $dirJson = Pasta (Join-Path (Join-Path $Destino 'resources') 'jsondata')
    Get-ChildItem $dirJson -Filter 't*.json' | Remove-Item -Confirm:$false
    $xml = New-Object System.Text.StringBuilder
    [void]$xml.AppendLine('<resources>')
    foreach ($k in $chaves) {
        ('[' + ($blocos[$k] -join ',') + ']') | Set-Content -Encoding ascii (Join-Path $dirJson "t$k.json")
        [void]$xml.AppendLine("    <jsonData id=`"t$k`" filename=`"t$k.json`" />")
    }
    [void]$xml.AppendLine('</resources>')
    Set-Content -Encoding ascii (Join-Path $dirJson 'tiles.xml') $xml.ToString()
    $mc = @(
        '// GERADO por radar-base/gerar_base.ps1 -Saida app. Nao editar a mao.'
        'import Toybox.Lang;'
        ''
        'module Tiles {'
        "    const TAM = $tamTxt;"
        "    const TOTAL = $($pts.Count);"
        "    const AREA = `"$Area`";"
        "    const ATUALIZADO = `"$hoje`";"
        # chaves ordenadas + recursos na mesma ordem: busca binaria (menos codigo que um if por bloco)
        '    const CHAVES = [' + ($chaves -join ',') + '];'
        '    function recurso(k as Number) as ResourceId or Null {'
        '        var a = 0;'
        '        var b = CHAVES.size() - 1;'
        '        while (a <= b) {'
        '            var m = (a + b) / 2;'
        '            var c = CHAVES[m];'
        '            if (c == k) { return recursos()[m]; }'
        '            if (c < k) { a = m + 1; } else { b = m - 1; }'
        '        }'
        '        return null;'
        '    }'
        '    function recursos() as Array<ResourceId> {'
        '        return [' + (($chaves | ForEach-Object { "Rez.JsonData.t$_" }) -join ',') + '];'
        '    }'
        '}'
    )
    Set-Content -Encoding ascii (Join-Path (Join-Path $Destino 'source') 'Tiles.mc') ($mc -join "`n")
}

$maior = ($blocos.Values | ForEach-Object { $_.Count / 5 } | Measure-Object -Maximum).Maximum
"Radares: $($pts.Count) ($nCet da CET: $nSemaf semaforo, $nLomb lombada, $nOnibus faixa de onibus; $nOsm do OSM; $nSentido com sentido pela via) em $($blocos.Count) blocos de $tamTxt graus (maior bloco: $maior)."
