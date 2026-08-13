#!/usr/bin/env python3
"""
Social Radar — Collector
Coleta métricas do Instagram, YouTube e TikTok e grava data/metrics.json.
Roda via cron na GCP VM, faz git commit + push automaticamente.

Instalação:
  pip install requests python-dotenv

Cron (diariamente às 07h):
  0 7 * * * cd /home/usuario/social-radar && python3 collector/collector.py >> /var/log/sr-collector.log 2>&1
"""

import os, sys, json, datetime, subprocess, logging
from pathlib import Path
from typing  import Optional

import requests
from dotenv import load_dotenv

# ─── CONFIG ──────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / '.env')

DATA_FILE   = Path(__file__).parent.parent / 'data' / 'metrics.json'
LOG_FMT     = '%(asctime)s [%(levelname)s] %(message)s'

# Máximo de posts por plataforma guardados no metrics.json (upsert_posts mantém
# só os mais recentes, descarta o resto). Com o Charla publicando 10-20 vídeos/
# dia no YouTube, 2.000 posts durava só uns 4-5 meses antes de vídeos mais
# antigos começarem a "sumir" do dashboard/relatórios (continuam no YouTube,
# só saem do nosso arquivo). Subido pra 6.000 — dá quase 1 ano de folga mesmo
# no ritmo mais puxado (20/dia).
MAX_POSTS   = 6000

# Coleta normal (diária, via cron) busca só a 1ª página de 100 posts mais recentes
# do Instagram — rápido, mas não "revisita" posts mais antigos (ex: se você editou
# a legenda/hashtag de um post de alguns dias atrás, ele não vai ser atualizado).
#
# Para forçar uma varredura completa (até 2.000 posts, ~15-20 min) — por exemplo,
# depois de editar hashtags de posts antigos no Instagram — rode assim:
#   python3 collector/collector.py --full
FULL_HISTORY   = '--full' in sys.argv
IG_MAX_PAGES   = 20 if FULL_HISTORY else 1

# YouTube: coleta normal só busca vídeos dos últimos 28 dias, então vídeos que
# saem dessa janela param de ser atualizados (views ficam "congeladas"). No modo
# --full, amplia a janela pra 1 ano.
#
# O Charla publica 10-20 vídeos/dia — isso pode encher até 560 vídeos só nos
# últimos 28 dias. Por isso pagina bastante em AMBOS os modos (não só no --full):
# 20 páginas x 50 = até 1.000 vídeos na coleta diária normal (folga confortável
# acima do pior caso de 560), e 40 páginas x 50 = até 2.000 no --full (cobre o
# ano inteiro até o teto do MAX_POSTS). Cada página custa 100 unidades de quota
# da YouTube Data API (limite padrão: 10.000/dia) — 20 páginas/dia = 2.000
# unidades, folga tranquila mesmo rodando todo dia.
YT_DAYS_BACK   = 365 if FULL_HISTORY else 28
YT_MAX_PAGES   = 40  if FULL_HISTORY else 20

# YouTube Analytics API (OAuth) — views diárias reais, watch time, duração média
# e engajamento por dia a nível de CANAL, além de watch time por VÍDEO (usado no
# relatório PDF). Só é ativado se os 3 secrets abaixo estiverem configurados:
#   YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
# Sem eles, o coletor cai de volta no comportamento antigo (Data API só, sem
# watch time, "views" do dia = total histórico do canal) — nada quebra.
YT_ANALYTICS_BASE = 'https://youtubeanalytics.googleapis.com/v2'
YT_TOKEN_URL       = 'https://oauth2.googleapis.com/token'
# Coleta normal só reconcilia os últimos dias (a API do YouTube Analytics tem
# atraso de 1-3 dias pra fechar os números de um dia). --full faz um backfill
# bem maior pra reconstruir o histórico de views diárias reais.
YT_ANALYTICS_DAYS_BACK = 400 if FULL_HISTORY else 5

logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger('sr-collector')

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def today() -> str:
    return datetime.date.today().isoformat()

def get_env(key: str, required: bool = True) -> Optional[str]:
    val = os.getenv(key)
    if required and not val:
        log.warning(f'Variável de ambiente não definida: {key}')
    return val

def load_metrics() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {
        'instagram': {'snapshots': [], 'posts': []},
        'youtube':   {'snapshots': [], 'posts': []},
        'tiktok':    {'snapshots': [], 'posts': []},
    }

def upsert_snapshot(metrics: dict, platform: str, snapshot: dict):
    """Insere ou atualiza snapshot do dia."""
    snaps = metrics[platform]['snapshots']
    date  = snapshot['date']
    idx   = next((i for i, s in enumerate(snaps) if s['date'] == date), -1)
    if idx >= 0:
        snaps[idx] = snapshot
    else:
        snaps.append(snapshot)
    snaps.sort(key=lambda s: s['date'])

def upsert_posts(metrics: dict, platform: str, posts: list):
    """Insere ou atualiza posts, mantendo os MAX_POSTS mais recentes."""
    existing = {p['id']: p for p in metrics[platform]['posts']}
    for post in posts:
        existing[post['id']] = post
    all_posts = sorted(existing.values(), key=lambda p: p['date'], reverse=True)
    metrics[platform]['posts'] = all_posts[:MAX_POSTS]

def save_metrics(metrics: dict):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    metrics['updated_at'] = datetime.datetime.now().isoformat()
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    log.info(f'metrics.json salvo em {DATA_FILE}')

def git_push():
    """Commit e push do metrics.json para o GitHub."""
    repo = DATA_FILE.parent.parent
    cmds = [
        ['git', '-C', str(repo), 'add', 'data/metrics.json'],
        ['git', '-C', str(repo), 'commit', '-m', f'data: update {today()}', '--allow-empty'],
        ['git', '-C', str(repo), 'push'],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and 'nothing to commit' not in result.stdout:
            log.warning(f'git: {result.stderr.strip()}')
        else:
            log.info(f'git: {" ".join(cmd[3:])} — ok')

# ─── INSTAGRAM: VIEWS DA CONTA (nível de conta, não por post) ─────────────────
def collect_instagram_account_views(metrics: dict, base: str, token: str, ig_user_id: Optional[str]) -> int:
    """
    Coleta a série diária de 'views' a nível de CONTA (todo o perfil, somando
    posts, reels, stories, buscas — é o mesmo número que aparece em "Painel
    profissional > visualizações" no app do Instagram), e também o 'reach' mais
    recente (alcance orgânico, métrica que continua válida e não foi descontinuada).

    Isso é DIFERENTE de somar as views de cada post no relatório: aquele soma
    inclui só posts publicados dentro do período filtrado, enquanto este número
    de conta inclui QUALQUER conteúdo (mesmo antigo) que gerou views durante o
    período. Os dois nunca vão bater — não é bug, são métricas diferentes.

    Retorna o valor de reach mais recente encontrado (pra alimentar o snapshot diário).
    """
    if not ig_user_id:
        log.warning('Instagram: INSTAGRAM_USER_ID não configurado — pulando views de conta')
        return 0
    metrics.setdefault('instagram', {})
    metrics['instagram'].setdefault('account_views', [])
    existing = {d['date']: d for d in metrics['instagram']['account_views']}
    latest_reach = 0

    # Coleta normal só recarrega os últimos dias (o valor de "hoje" muda ao
    # longo do dia). --full faz um backfill maior pra reconstruir o histórico.
    days_back = 90 if FULL_HISTORY else 3
    end   = datetime.datetime.utcnow().date()
    start = end - datetime.timedelta(days=days_back)

    # period=day só aceita janelas de até ~30 dias por chamada — pagina em blocos
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + datetime.timedelta(days=29), end)
        try:
            r = requests.get(f'{base}/{ig_user_id}/insights', params={
                'metric':       'views,reach',
                'period':       'day',
                'metric_type':  'time_series',
                'since':        cursor.strftime('%Y-%m-%d'),
                'until':        chunk_end.strftime('%Y-%m-%d'),
                'access_token': token,
            }, timeout=15)
            if r.ok:
                for item in r.json().get('data', []):
                    if item.get('name') == 'views':
                        for v in item.get('values', []):
                            d = (v.get('end_time') or '')[:10]
                            if d:
                                existing[d] = {'date': d, 'views': int(v.get('value', 0) or 0)}
                    elif item.get('name') == 'reach':
                        vals = item.get('values', [])
                        if vals:
                            latest_reach = int(vals[-1].get('value', 0) or 0)
            else:
                log.warning(f'Instagram account views ({cursor}–{chunk_end}): HTTP {r.status_code} — {r.text[:200]}')
        except Exception as e:
            log.warning(f'Instagram account views ({cursor}–{chunk_end}): {e}')
        cursor = chunk_end + datetime.timedelta(days=1)

    metrics['instagram']['account_views'] = sorted(existing.values(), key=lambda d: d['date'])
    log.info(f"Instagram: {len(metrics['instagram']['account_views'])} dias de views de conta armazenados")
    return latest_reach

# ─── INSTAGRAM ───────────────────────────────────────────────────────────────
def collect_instagram(metrics: dict) -> bool:
    token = get_env('INSTAGRAM_ACCESS_TOKEN')
    if not token:
        return False

    base = 'https://graph.facebook.com/v22.0'

    # Dados do perfil
    try:
        r = requests.get(f'{base}/17841445654660624', params={
            'fields':       'followers_count,follows_count,media_count,biography',
            'access_token': token,
        }, timeout=15)
        r.raise_for_status()
        profile = r.json()
        log.info(f'Instagram: {profile.get("followers_count", "?")} seguidores')
    except Exception as e:
        log.error(f'Instagram profile: {e}')
        return False

    # Views da CONTA INTEIRA (não por post) — é o número que aparece como
    # "visualizações" no Painel Profissional do app do Instagram (ex: "65,5 mi
    # visualizações nos últimos 30 dias"). Guarda uma série diária pra dar pra
    # somar certinho por qualquer período escolhido no dashboard.
    #
    # Nota: até abril/2025 essa métrica se chamava 'impressions'/'profile_views',
    # mas a Meta descontinuou os dois e unificou tudo em 'views' (Graph API v22+).
    # Código antigo que ainda chamava os nomes velhos falhava silenciosamente.
    ig_user_id = get_env('INSTAGRAM_USER_ID', required=False)
    latest_reach = collect_instagram_account_views(metrics, base, token, ig_user_id)

    # Pega o valor de hoje (se já disponível) na série que acabou de ser coletada
    today_account_views = next(
        (d['views'] for d in metrics['instagram'].get('account_views', []) if d['date'] == today()),
        0
    )

    snapshot = {
        'date':        today(),
        'followers':   profile.get('followers_count', 0),
        'following':   profile.get('follows_count',   0),
        'media_count': profile.get('media_count',     0),
        'views':       today_account_views,
        'reach':       latest_reach,
    }

    # Posts recentes
    posts = []
    try:
        url = f'{base}/17841445654660624/media'
        params = {'fields':'id,caption,media_type,timestamp,like_count,comments_count,permalink','limit':100,'access_token':token}
        all_items = []
        page = 0
        while url and page < IG_MAX_PAGES:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            rd = r.json()
            all_items.extend(rd.get('data', []))
            url = rd.get('paging', {}).get('next')
            params = {}
            page += 1
        insights_failures = 0
        for item in all_items:
            d     = item['timestamp'][:10]
            views = reach = shares = saves = 0

            # Métricas de post (só para reels/vídeos — imagem não tem "views")
            # Nota sobre nomes: o que o Instagram chama de "views" hoje é o que
            # antes se chamava "impressions" (a Meta só renomeou a métrica em
            # 2025 — é a mesma coisa). "Reach" é "alcance". Isso é usado pra
            # montar relatórios no formato que patrocinadores pedem (ex: Assaí).
            if item.get('media_type') in ('VIDEO', 'REELS'):
                try:
                    rv = requests.get(f'{base}/{item["id"]}/insights', params={
                        'metric':       'views,reach,shares,saved',
                        'access_token': token,
                    }, timeout=10)
                    if rv.ok:
                        for m in rv.json().get('data', []):
                            val = m.get('values', [{}])[-1].get('value', 0)
                            if m['name'] == 'views':  views  = val
                            elif m['name'] == 'reach': reach  = val
                            elif m['name'] == 'shares': shares = val
                            elif m['name'] == 'saved':  saves  = val
                    else:
                        insights_failures += 1
                        if insights_failures <= 3:  # não spamma o log, só mostra os primeiros
                            log.warning(f'Instagram insights do post {item["id"]}: HTTP {rv.status_code} — {rv.text[:200]}')
                except Exception as e:
                    insights_failures += 1
                    if insights_failures <= 3:
                        log.warning(f'Instagram insights do post {item["id"]}: {e}')

            posts.append({
                'id':          item['id'],
                'date':        d,
                'caption':     (item.get('caption') or '')[:120],
                'url':         item.get('permalink',''),
                'tags':        [w.lower() for w in (item.get('caption') or '').split() if w.startswith('#')][:10],
                'type':        item.get('media_type', 'IMAGE'),
                'views':       views,     # = "impressões" no vocabulário de patrocinador
                'reach':       reach,     # = "alcance/visualizações"
                'likes':       item.get('like_count',    0),
                'comments':    item.get('comments_count', 0),
                'shares':      shares,
                'saves':       saves,
            })
        if insights_failures:
            log.warning(f'Instagram: falha ao buscar alcance/compart./salvos em {insights_failures} de {len(all_items)} posts (views/likes/comentários não são afetados)')
    except Exception as e:
        log.error(f'Instagram media: {e}')

    upsert_snapshot(metrics, 'instagram', snapshot)
    upsert_posts(metrics, 'instagram', posts)

    # Soma likes do dia baseado nos posts recentes de hoje
    today_likes = sum(p['likes'] for p in posts if p['date'] == today())
    snapshot['likes'] = today_likes
    log.info(f'Instagram: {len(posts)} posts coletados')
    return True

# ─── YOUTUBE ANALYTICS (OAuth) ────────────────────────────────────────────────
def youtube_get_access_token() -> Optional[str]:
    """
    Troca o refresh_token (obtido uma vez via OAuth Playground, com validade
    indefinida porque o app está publicado em produção — não em modo "Teste",
    que limitaria o refresh_token a 7 dias) por um access_token novo, válido
    por ~1h. Precisa dos secrets YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET e
    YOUTUBE_REFRESH_TOKEN — sem eles, a integração com YouTube Analytics fica
    desativada e o coletor cai de volta no comportamento antigo (só Data API,
    sem watch time, "views" do dia = total histórico do canal).
    """
    client_id     = get_env('YOUTUBE_CLIENT_ID',     required=False)
    client_secret = get_env('YOUTUBE_CLIENT_SECRET', required=False)
    refresh_token = get_env('YOUTUBE_REFRESH_TOKEN', required=False)
    if not (client_id and client_secret and refresh_token):
        return None
    try:
        r = requests.post(YT_TOKEN_URL, data={
            'grant_type':    'refresh_token',
            'refresh_token': refresh_token,
            'client_id':     client_id,
            'client_secret': client_secret,
        }, timeout=15)
        if r.ok:
            return r.json().get('access_token')
        log.error(f'YouTube Analytics: falha ao renovar access token — HTTP {r.status_code}: {r.text[:200]}')
    except Exception as e:
        log.error(f'YouTube Analytics: erro ao renovar access token — {e}')
    return None

def collect_youtube_analytics(metrics: dict, access_token: str, channel_id: str) -> Optional[dict]:
    """
    Busca a série diária REAL do YouTube Analytics API a nível de CANAL: views,
    watch time, duração média, likes/comentários/compart. e inscritos ganhos
    por dia. Resolve em definitivo o bug #10 ("Views Totais" mostrando 15+
    bilhões porque o coletor somava o total histórico do canal repetido em
    cada dia do período).

    IMPORTANTE sobre o 'ids': usamos 'channel==<ID>' (o mesmo YOUTUBE_CHANNEL_ID
    já configurado, que a Data API já usa e confirmadamente resolve pro canal
    certo) — NÃO 'channel==MINE'. 'MINE' resolve pro canal padrão vinculado à
    conta Google autenticada, que pode ser diferente do canal (Conta de Marca)
    que essa conta apenas administra. Foi exatamente isso que causou o bug
    descoberto em produção: 'MINE' devolvia um canal quase vazio (poucos vídeos,
    views de dígito único) em vez do Charla Podcast de verdade.

    IMPORTANTE — o que essa API NÃO fornece (limitação do próprio YouTube, não
    nossa): "Impressões" e "CTR de impressões" (cliques na miniatura) só
    existem dentro do YouTube Studio — não são expostas por nenhuma API
    pública. "Visualizadores únicos" (uniques) foi descontinuado pelo YouTube
    em 2016. Não tentamos buscar nenhum dos três porque não tem como.

    Grava a série em metrics['youtube']['analytics_daily'][] — igual ao
    account_views do Instagram: um array separado de 'snapshots' (que só
    ganha 1 entrada por execução do coletor), pra permitir reconciliar dias
    passados assim que a API fecha os números (atraso normal de 1-3 dias).

    Retorna os dados de HOJE (se já disponíveis) pra alimentar o snapshot do
    dia, ou None se ainda não tiver fechado.
    """
    metrics['youtube'].setdefault('analytics_daily', [])
    existing = {d['date']: d for d in metrics['youtube']['analytics_daily']}

    end   = datetime.datetime.utcnow().date()
    start = end - datetime.timedelta(days=YT_ANALYTICS_DAYS_BACK)

    try:
        r = requests.get(f'{YT_ANALYTICS_BASE}/reports', params={
            'ids':        f'channel=={channel_id}',
            'startDate':  start.strftime('%Y-%m-%d'),
            'endDate':    end.strftime('%Y-%m-%d'),
            'dimensions': 'day',
            'metrics':    'views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares,subscribersGained',
            'sort':       'day',
        }, headers={'Authorization': f'Bearer {access_token}'}, timeout=30)
        if not r.ok:
            log.warning(f'YouTube Analytics (série diária): HTTP {r.status_code} — {r.text[:300]}')
            return existing.get(today())
        data = r.json()
        cols = [c['name'] for c in data.get('columnHeaders', [])]
        for row in data.get('rows', []):
            rec = dict(zip(cols, row))
            d = rec.get('day')
            if not d:
                continue
            existing[d] = {
                'date':                  d,
                'views':                 int(rec.get('views', 0) or 0),
                'watch_minutes':         int(rec.get('estimatedMinutesWatched', 0) or 0),
                'avg_view_duration_sec': int(rec.get('averageViewDuration', 0) or 0),
                'likes':                 int(rec.get('likes', 0) or 0),
                'comments':              int(rec.get('comments', 0) or 0),
                'shares':                int(rec.get('shares', 0) or 0),
                'subscribers_gained':    int(rec.get('subscribersGained', 0) or 0),
            }
    except Exception as e:
        log.error(f'YouTube Analytics (série diária): {e}')
        return existing.get(today())

    metrics['youtube']['analytics_daily'] = sorted(existing.values(), key=lambda d: d['date'])
    log.info(f"YouTube Analytics: {len(metrics['youtube']['analytics_daily'])} dias de dados reais armazenados")
    return existing.get(today())

def reconcile_youtube_snapshots(metrics: dict, analytics_by_date: dict):
    """
    Corrige snapshots dos últimos dias com os números reais assim que eles
    fecham na API (atraso de 1-3 dias). Sem isso, um snapshot salvo no dia em
    que os dados ainda não tinham fechado ficaria com o total histórico do
    canal (valor antigo/errado) pra sempre, em vez de ser corrigido no dia
    seguinte quando os dados reais já estiverem disponíveis.
    """
    for snap in metrics['youtube']['snapshots']:
        d = analytics_by_date.get(snap['date'])
        if not d:
            continue
        snap['views']                 = d['views']
        snap['watch_minutes']         = d['watch_minutes']
        snap['avg_view_duration_sec'] = d['avg_view_duration_sec']
        snap['subscribers_gained']    = d['subscribers_gained']
        # Likes/comentários/compart. reais do CANAL INTEIRO naquele dia (todo
        # o conteúdo, não só vídeos publicados naquele dia) — mais precisos
        # que a soma feita a partir só dos vídeos novos do dia, então
        # substituem esse valor.
        snap['likes']    = d['likes']
        snap['comments'] = d['comments']
        snap['shares']   = d['shares']

def collect_youtube_video_analytics(video_ids: list, access_token: str, channel_id: str) -> dict:
    """
    Busca watch time e duração média POR VÍDEO (dimensions=video), em lotes de
    até 200 IDs (o filtro 'video==' aceita até 500 IDs por chamada, mas esse
    tipo de relatório limita maxResults a 200 — lotes maiores perderiam vídeos
    silenciosamente). Usado pra preencher a coluna "Watch Time" real no
    relatório PDF de patrocinador (antes ficava sempre fixa em "—").

    Usa 'channel==<ID>' (não 'channel==MINE' — ver nota em collect_youtube_analytics
    sobre por que 'MINE' pode resolver pro canal errado quando o canal é uma
    Conta de Marca administrada, não o canal padrão da conta Google logada).

    Retorna {video_id: {watch_minutes, avg_view_duration_sec}}.
    """
    if not video_ids:
        return {}
    out = {}
    for i in range(0, len(video_ids), 200):
        batch = video_ids[i:i + 200]
        try:
            r = requests.get(f'{YT_ANALYTICS_BASE}/reports', params={
                'ids':        f'channel=={channel_id}',
                'startDate':  '2005-01-01',   # cobre qualquer vídeo já publicado no canal
                'endDate':    today(),
                'dimensions': 'video',
                'metrics':    'views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares',
                'filters':    'video==' + ','.join(batch),
                'maxResults': 200,
                'sort':       '-views',
            }, headers={'Authorization': f'Bearer {access_token}'}, timeout=30)
            if not r.ok:
                log.warning(f'YouTube Analytics (watch time por vídeo, lote {i // 200 + 1}): HTTP {r.status_code} — {r.text[:200]}')
                continue
            data = r.json()
            cols = [c['name'] for c in data.get('columnHeaders', [])]
            rows = data.get('rows', [])
            log.info(f'YouTube Analytics (watch time por vídeo, lote {i // 200 + 1}): {len(rows)} de {len(batch)} vídeos retornaram dados (colunas: {cols})')
            for row in rows:
                rec = dict(zip(cols, row))
                vid = rec.get('video')
                if not vid:
                    continue
                out[vid] = {
                    'watch_minutes':         int(rec.get('estimatedMinutesWatched', 0) or 0),
                    'avg_view_duration_sec': int(rec.get('averageViewDuration', 0) or 0),
                }
        except Exception as e:
            log.warning(f'YouTube Analytics (watch time por vídeo, lote {i // 200 + 1}): {e}')
    return out

# ─── YOUTUBE ─────────────────────────────────────────────────────────────────
def collect_youtube(metrics: dict) -> bool:
    api_key    = get_env('YOUTUBE_API_KEY')
    channel_id = get_env('YOUTUBE_CHANNEL_ID')
    if not api_key or not channel_id:
        return False

    base = 'https://www.googleapis.com/youtube/v3'

    # Estatísticas do canal
    try:
        r = requests.get(f'{base}/channels', params={
            'part': 'statistics,snippet',
            'id':   channel_id,
            'key':  api_key,
        }, timeout=15)
        r.raise_for_status()
        items = r.json().get('items', [])
        if not items:
            log.error('YouTube: canal não encontrado')
            return False
        stats = items[0]['statistics']
        log.info(f'YouTube: {stats.get("subscriberCount", "?")} inscritos')
    except Exception as e:
        log.error(f'YouTube channel: {e}')
        return False

    snapshot = {
        'date':        today(),
        'followers':   int(stats.get('subscriberCount', 0)),
        'views':       int(stats.get('viewCount',       0)),  # fallback: total histórico do canal —
                                                                # só fica assim se o YouTube Analytics
                                                                # não estiver configurado (ver abaixo)
        'video_count': int(stats.get('videoCount',      0)),
        'likes':       0,   # agregado abaixo
        'comments':    0,
        'shares':      0,
    }

    # Vídeos recentes (28 dias na coleta normal; até 1 ano no modo --full)
    published_after = (datetime.datetime.utcnow() - datetime.timedelta(days=YT_DAYS_BACK)).strftime('%Y-%m-%dT%H:%M:%SZ')
    video_ids = []
    page_token = None
    try:
        for _ in range(YT_MAX_PAGES):
            params = {
                'part':           'snippet',
                'channelId':      channel_id,
                'type':           'video',
                'order':          'date',
                'maxResults':     50,   # máximo permitido pela API — usado nos dois modos
                'publishedAfter': published_after,
                'key':            api_key,
            }
            if page_token:
                params['pageToken'] = page_token
            r = requests.get(f'{base}/search', params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            for item in data.get('items', []):
                video_ids.append(item['id']['videoId'])
            page_token = data.get('nextPageToken')
            if not page_token:
                break
    except Exception as e:
        log.error(f'YouTube search: {e}')

    posts = []
    if video_ids:
        try:
            # /videos só aceita até 50 IDs por chamada — quebra em blocos
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i:i+50]
                r = requests.get(f'{base}/videos', params={
                    'part': 'snippet,statistics',
                    'id':   ','.join(batch),
                    'key':  api_key,
                }, timeout=15)
                r.raise_for_status()
                for item in r.json().get('items', []):
                    s = item['statistics']
                    d = item['snippet']['publishedAt'][:10]
                    posts.append({
                        'id':       item['id'],
                        'date':     d,
                        'caption':  item['snippet'].get('title', '')[:120],
                        'type':     'VIDEO',
                        'views':    int(s.get('viewCount',    0)),
                        'likes':    int(s.get('likeCount',    0)),
                        'comments': int(s.get('commentCount', 0)),
                        'shares':   0,
                        'saves':    int(s.get('favoriteCount', 0)),
                    })
            # Agrega likes/comments do dia nos snapshots (pode ser substituído
            # abaixo pelos números reais do YouTube Analytics, se disponíveis)
            today_posts = [p for p in posts if p['date'] == today()]
            snapshot['likes']    = sum(p['likes']    for p in today_posts)
            snapshot['comments'] = sum(p['comments'] for p in today_posts)
        except Exception as e:
            log.error(f'YouTube videos: {e}')

    # ── YouTube Analytics (OAuth) — só roda se os 3 secrets estiverem configurados ──
    access_token = youtube_get_access_token()
    if access_token:
        # 1) Série diária a nível de canal: corrige 'views' do dia (bug #10),
        #    e adiciona watch time / duração média / inscritos ganhos reais.
        today_analytics = collect_youtube_analytics(metrics, access_token, channel_id)
        analytics_by_date = {d['date']: d for d in metrics['youtube'].get('analytics_daily', [])}
        reconcile_youtube_snapshots(metrics, analytics_by_date)
        if not today_analytics:
            log.info('YouTube Analytics: dados de hoje ainda não fechados na API (atraso normal de 1-3 dias) — corrigido automaticamente no próximo dia')

        # 2) Watch time por vídeo — preenche a coluna "Watch Time" do relatório PDF
        if posts:
            vid_analytics = collect_youtube_video_analytics([p['id'] for p in posts], access_token, channel_id)
            enriched = 0
            for p in posts:
                va = vid_analytics.get(p['id'])
                if va:
                    p['watch_minutes']         = va['watch_minutes']
                    p['avg_view_duration_sec'] = va['avg_view_duration_sec']
                    enriched += 1
            if enriched:
                log.info(f'YouTube Analytics: watch time real coletado para {enriched} de {len(posts)} vídeos')
            else:
                log.info(f'YouTube Analytics: nenhum dos {len(posts)} vídeos tinha watch time disponível ainda (normal para vídeos muito recentes — a API tem o mesmo atraso de 1-3 dias das views diárias; deve aparecer nos próximos dias)')
    else:
        log.info('YouTube Analytics: secrets OAuth não configurados — "views" continua sendo o total histórico do canal e o relatório PDF fica sem watch time real (comportamento antigo, nada quebra)')

    upsert_snapshot(metrics, 'youtube', snapshot)
    upsert_posts(metrics, 'youtube', posts)
    log.info(f'YouTube: {len(posts)} vídeos coletados')
    return True

# ─── TIKTOK ──────────────────────────────────────────────────────────────────
def collect_tiktok(metrics: dict) -> bool:
    token = get_env('TIKTOK_ACCESS_TOKEN')
    if not token:
        return False

    base    = 'https://open.tiktokapis.com/v2'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
    }

    # Informações do usuário
    try:
        r = requests.get(f'{base}/user/info/', headers=headers, params={
            'fields': 'display_name,bio_description,follower_count,following_count,likes_count,video_count',
        }, timeout=15)
        r.raise_for_status()
        user = r.json().get('data', {}).get('user', {})
        log.info(f'TikTok: {user.get("follower_count", "?")} seguidores')
    except Exception as e:
        log.error(f'TikTok user: {e}')
        return False

    snapshot = {
        'date':        today(),
        'followers':   user.get('follower_count',  0),
        'following':   user.get('following_count', 0),
        'total_likes': user.get('likes_count',     0),
        'video_count': user.get('video_count',     0),
        'views':       0,   # agregado dos vídeos abaixo
        'likes':       0,
        'comments':    0,
        'shares':      0,
    }

    # Lista de vídeos recentes
    posts = []
    try:
        payload = {
            'max_count': 20,
            'fields': ['id', 'title', 'create_time', 'view_count', 'like_count',
                       'comment_count', 'share_count', 'duration', 'cover_image_url'],
        }
        r = requests.post(f'{base}/video/list/', headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        videos = r.json().get('data', {}).get('videos', [])
        for v in videos:
            ts   = v.get('create_time', 0)
            date = datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d') if ts else today()
            posts.append({
                'id':       v.get('id',            ''),
                'date':     date,
                'caption':  (v.get('title') or '')[:120],
                'type':     'VIDEO',
                'views':    v.get('view_count',    0),
                'likes':    v.get('like_count',    0),
                'comments': v.get('comment_count', 0),
                'shares':   v.get('share_count',   0),
                'saves':    0,
            })
        # Agrega métricas no snapshot
        snapshot['views']    = sum(p['views']    for p in posts)
        snapshot['likes']    = sum(p['likes']    for p in posts)
        snapshot['comments'] = sum(p['comments'] for p in posts)
        snapshot['shares']   = sum(p['shares']   for p in posts)
    except Exception as e:
        log.error(f'TikTok videos: {e}')

    upsert_snapshot(metrics, 'tiktok', snapshot)
    upsert_posts(metrics, 'tiktok', posts)
    log.info(f'TikTok: {len(posts)} vídeos coletados')
    return True

# ─── TOKEN REFRESH (Instagram) ───────────────────────────────────────────────
def refresh_instagram_token():
    """
    Tokens de longa duração expiram em 60 dias.
    Execute manualmente ou adicione ao cron mensal.
    """
    token = get_env('INSTAGRAM_ACCESS_TOKEN')
    if not token:
        return
    try:
        r = requests.get('https://graph.instagram.com/refresh_access_token', params={
            'grant_type':   'ig_refresh_token',
            'access_token': token,
        }, timeout=15)
        if r.ok:
            new_token = r.json().get('access_token')
            log.info(f'Token Instagram renovado. Atualize .env com: {new_token[:20]}...')
        else:
            log.error(f'Falha ao renovar token: {r.text}')
    except Exception as e:
        log.error(f'Token refresh: {e}')

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log.info('=' * 50)
    log.info(f'Social Radar Collector — {today()}')
    if FULL_HISTORY:
        log.info('Modo --full ativado: varredura completa do Instagram (até 2.000 posts) e do YouTube (até 2.000 vídeos, janela de 1 ano, backfill de ~400 dias de YouTube Analytics) — pode levar bem mais que os 15-20 min de antes, dado o volume de vídeos/dia do canal')
    log.info('=' * 50)

    metrics = load_metrics()
    results = {}

    results['instagram'] = collect_instagram(metrics)
    results['youtube']   = collect_youtube(metrics)
    results['tiktok']    = collect_tiktok(metrics)

    ok = [p for p, v in results.items() if v]
    ko = [p for p, v in results.items() if not v]

    if ok:
        save_metrics(metrics)
        log.info(f'Coleta concluída: {ok}')
        if ko:
            log.warning(f'Plataformas com falha/sem token: {ko}')
        git_push()
    else:
        log.error('Nenhuma plataforma coletada. Abortando.')
        sys.exit(1)

if __name__ == '__main__':
    if '--refresh-token' in sys.argv:
        refresh_instagram_token()
    else:
        main()


# ─── AUTO-TAGGING ─────────────────────────────────────────────────────────────
_PAID_SIGNALS    = ['publi', 'publicidade', 'parceria', 'patrocin', '#ad', '#publi',
                    'em parceria', 'apoio de', 'conteúdo pago', 'link na bio']
_CONTENT_SIGNALS = {
    '#gol':        ['gol', 'golaço', 'marcou', 'placar'],
    '#bastidores': ['bastidor', 'nos bastidores', 'câmera', 'exclusivo'],
    '#debate':     ['debate', 'análise', 'tática', 'opinião'],
    '#reação':     ['reação', 'react', 'reagindo', 'assistindo'],
    '#exclusiva':  ['exclusiva', 'entrevista', 'confessou', 'revelou'],
    '#ao-vivo':    ['ao vivo', 'live', 'transmissão'],
    '#copa':       ['copa', 'mundial', 'campeonato', 'torneio'],
}

def auto_tags(caption: str) -> list:
    if not caption:
        return ['#organico']
    low = caption.lower()
    tags = []
    if any(s in low for s in _PAID_SIGNALS):
        tags.append('#pago')
    else:
        tags.append('#organico')
    for tag, signals in _CONTENT_SIGNALS.items():
        if any(s in low for s in signals):
            tags.append(tag)
    return tags
