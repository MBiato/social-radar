#!/usr/bin/env python3
"""
Charles Weekly Report — Gera resumo semanal todo domingo via Claude API
Salva em data/weekly/YYYY-WNN.json
"""
import json, os, sys, requests
from datetime import datetime, timedelta
from pathlib import Path

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY_CHARLES_INSIGHTS')
METRICS_PATH      = Path('data/metrics.json')
WEEKLY_DIR        = Path('data/weekly')

SPONSORS = {
    '#ForneriaNoCharla':    'Forneria',
    '#SportingbetNoCharla': 'Sportingbet',
    '#MelittaNoCharla':     'Melitta',
    '#BrahmaNoCharla':      'Brahma',
    '#TexacoNoCharla':      'Texaco',
    '#AssaiNoCharla':       'Assaí',
    '#AbsolutNoCharla':     'Absolut',
    '#ClearNoCharla':       'Clear',
}

def fmt(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def get_week_range():
    today = datetime.utcnow().date()
    # Últimos 7 dias
    end   = today - timedelta(days=1)
    start = end - timedelta(days=6)
    return str(start), str(end)

def get_week_posts(metrics, start, end):
    posts = []
    for plat in ['instagram', 'youtube', 'tiktok']:
        for p in metrics.get(plat, {}).get('posts', []):
            d = p.get('date','')
            if start <= d <= end:
                posts.append({**p, 'platform': plat})
    return posts

def get_sponsor_stats(posts):
    stats = {}
    for p in posts:
        for tag in (p.get('tags') or []):
            name = SPONSORS.get(tag, tag)
            if name not in stats:
                stats[name] = {'posts':0,'views':0,'likes':0,'comments':0,'tag':tag}
            stats[name]['posts']    += 1
            stats[name]['views']    += p.get('views',0)
            stats[name]['likes']    += p.get('likes',0)
            stats[name]['comments'] += p.get('comments',0)
    # Ordenar por views
    return dict(sorted(stats.items(), key=lambda x: x[1]['views'], reverse=True))

def generate_weekly_summary(posts, sponsor_stats, start, end):
    total_views    = sum(p.get('views',0) for p in posts)
    total_likes    = sum(p.get('likes',0) for p in posts)
    total_comments = sum(p.get('comments',0) for p in posts)
    ig_posts = [p for p in posts if p.get('platform')=='instagram']
    yt_posts = [p for p in posts if p.get('platform')=='youtube']
    top3 = sorted(posts, key=lambda p: p.get('views',0), reverse=True)[:3]

    sponsor_summary = '\n'.join([
        f"- {name}: {s['posts']} posts, {fmt(s['views'])} views, {fmt(s['likes']+s['comments'])} interações"
        for name, s in list(sponsor_stats.items())[:5]
    ])

    top3_summary = '\n'.join([
        f"- [{p['platform']}] {p.get('caption','')[:100]} → {fmt(p.get('views',0))} views"
        for p in top3
    ])

    prompt = f"""Você é Charles, analista do Charla Podcast. Gere um relatório semanal com base nos dados abaixo. Responda APENAS em JSON válido:

PERÍODO: {start} a {end}
TOTAL DE POSTS: {len(posts)} (Instagram: {len(ig_posts)}, YouTube: {len(yt_posts)})
TOTAL DE VIEWS: {fmt(total_views)}
TOTAL DE LIKES: {fmt(total_likes)}
TOTAL DE COMENTÁRIOS: {fmt(total_comments)}

TOP 3 POSTS:
{top3_summary}

PATROCINADORES:
{sponsor_summary}

JSON esperado:
{{
  "resumo_semana": "<resumo executivo da semana em 3-4 frases>",
  "destaque_semana": "<o momento mais marcante da semana>",
  "padroes": ["<padrão 1>", "<padrão 2>", "<padrão 3>"],
  "recomendacoes": ["<recomendação 1 para próxima semana>", "<recomendação 2>", "<recomendação 3>"],
  "analise_patrocinadores": "<análise geral de como as marcas performaram esta semana em 2-3 frases>",
  "proximo_foco": "<sugestão de conteúdo ou formato para focar na próxima semana>"
}}"""

    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-haiku-4-5',
                'max_tokens': 700,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=30
        )
        r.raise_for_status()
        text = r.json()['content'][0]['text'].strip()
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'): text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        print(f"Erro no resumo semanal: {e}", file=sys.stderr)
        return {
            "resumo_semana": "—", "destaque_semana": "—",
            "padroes": [], "recomendacoes": [],
            "analise_patrocinadores": "—", "proximo_foco": "—"
        }

def main():
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY_CHARLES_INSIGHTS não definida", file=sys.stderr)
        sys.exit(1)
    if not METRICS_PATH.exists():
        print("metrics.json não encontrado", file=sys.stderr)
        sys.exit(1)

    with open(METRICS_PATH) as f:
        metrics = json.load(f)

    start, end = get_week_range()
    print(f"Gerando relatório semanal: {start} a {end}...")

    posts = get_week_posts(metrics, start, end)
    if not posts:
        print("Nenhum post encontrado na semana.")
        return

    print(f"  {len(posts)} posts encontrados")
    sponsor_stats = get_sponsor_stats(posts)
    ai_summary    = generate_weekly_summary(posts, sponsor_stats, start, end)

    ig_posts = [p for p in posts if p.get('platform')=='instagram']
    yt_posts = [p for p in posts if p.get('platform')=='youtube']
    top_posts = sorted(posts, key=lambda p: p.get('views',0), reverse=True)[:10]

    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "period_start": start,
        "period_end":   end,
        "week_label":   f"{start} a {end}",
        "metrics": {
            "total_posts":    len(posts),
            "instagram_posts": len(ig_posts),
            "youtube_posts":   len(yt_posts),
            "total_views":    sum(p.get('views',0) for p in posts),
            "total_likes":    sum(p.get('likes',0) for p in posts),
            "total_comments": sum(p.get('comments',0) for p in posts),
        },
        "sponsor_stats": sponsor_stats,
        "top_posts":     top_posts,
        "ai_summary":    ai_summary,
    }

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    # Nome do arquivo: ano + número da semana ISO
    week_num = datetime.strptime(start, '%Y-%m-%d').isocalendar()
    filename = f"{week_num[0]}-W{week_num[1]:02d}.json"
    report_path = WEEKLY_DIR / filename
    with open(report_path, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Também salvar como latest para o dashboard acessar facilmente
    latest_path = WEEKLY_DIR / 'latest.json'
    with open(latest_path, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Relatório semanal salvo: {report_path}")
    print(f"  Posts: {len(posts)} | Views: {fmt(report['metrics']['total_views'])} | Patrocinadores: {len(sponsor_stats)}")

if __name__ == '__main__':
    main()
