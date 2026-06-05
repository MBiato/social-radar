#!/usr/bin/env python3
"""
Charles Insights — Analisa posts do dia via Claude API
Gera data/insights.json com análises, scores e sugestões
"""
import json, os, sys, requests
from datetime import datetime, timedelta
from pathlib import Path

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY_CHARLES_INSIGHTS')
METRICS_PATH      = Path('data/metrics.json')
INSIGHTS_PATH     = Path('data/insights.json')

def fmt(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def get_yesterday():
    return (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

def get_posts_yesterday(metrics, date):
    posts = []
    for plat in ['instagram', 'youtube', 'tiktok']:
        for p in metrics.get(plat, {}).get('posts', []):
            if p.get('date', '') == date:
                posts.append({**p, 'platform': plat})
    return posts

def analyze_post(post):
    plat = post['platform']
    views = post.get('views', 0)
    likes = post.get('likes', 0)
    comments = post.get('comments', 0)
    caption = post.get('caption', '')[:300]
    tags = ' '.join(post.get('tags', []))
    url = post.get('url', '')
    ptype = post.get('type', '')

    platform_tips = {
        'instagram': "boas práticas do Instagram: hooks fortes em caixa alta, rostos na thumbnail, CTAs para salvar, hashtags temáticas, duração ideal de Reels entre 15-90s.",
        'youtube': "boas práticas do YouTube: títulos com nome próprio + emoção, thumbnails com rosto expressivo + texto sobreposto, capítulos na descrição, watch time acima de 50%.",
        'tiktok': "boas práticas do TikTok: hook nos primeiros 2 segundos, trending sounds, textos curtos na tela, duração entre 15-60s para máximo alcance."
    }

    prompt = f"""Você é Charles, o analista de conteúdo do Charla Podcast. Analise este post e responda APENAS em JSON válido, sem markdown.

POST:
- Plataforma: {plat}
- Tipo: {ptype}
- Data: {post.get('date')}
- Views: {fmt(views)}
- Likes: {fmt(likes)}
- Comentários: {fmt(comments)}
- Caption/Título: {caption}
- Hashtags: {tags}
- Link: {url}

Contexto: {platform_tips.get(plat, '')}

Responda SOMENTE com este JSON (sem texto extra):
{{
  "score": <número de 0 a 10>,
  "status": "<destaque|normal|alerta>",
  "resumo": "<1 frase resumindo a performance>",
  "thumb": {{"nota": "<bom|medio|ruim>", "analise": "<análise em 1-2 frases>", "sugestao": "<sugestão prática>"}},
  "titulo": {{"nota": "<bom|medio|ruim>", "analise": "<análise em 1-2 frases>", "sugestao": "<sugestão prática>"}},
  "legenda": {{"nota": "<bom|medio|ruim>", "analise": "<análise em 1-2 frases>", "sugestao": "<sugestão prática>"}},
  "causa_baixa": "<se status=alerta, explique o possível motivo em 2-3 frases, senão deixe vazio>",
  "padrao": "<padrão identificado neste post em 1 frase>"
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
                'max_tokens': 600,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=30
        )
        r.raise_for_status()
        text = r.json()['content'][0]['text'].strip()
        # Limpar possível markdown
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        print(f"  Erro ao analisar post {post.get('id')}: {e}", file=sys.stderr)
        return {
            "score": 5, "status": "normal",
            "resumo": "Análise indisponível.",
            "thumb": {"nota": "medio", "analise": "—", "sugestao": "—"},
            "titulo": {"nota": "medio", "analise": "—", "sugestao": "—"},
            "legenda": {"nota": "medio", "analise": "—", "sugestao": "—"},
            "causa_baixa": "", "padrao": "—"
        }

def generate_summary(posts_analyzed):
    if not posts_analyzed:
        return {}
    scores = [p['analysis']['score'] for p in posts_analyzed if 'analysis' in p]
    avg_score = round(sum(scores)/len(scores), 1) if scores else 0
    best = max(posts_analyzed, key=lambda p: p.get('analysis',{}).get('score',0))
    alerts = [p for p in posts_analyzed if p.get('analysis',{}).get('status') == 'alerta']
    padroes = [p['analysis'].get('padrao','') for p in posts_analyzed if 'analysis' in p and p['analysis'].get('padrao')]

    prompt = f"""Você é Charles, analista do Charla Podcast. Com base nos {len(posts_analyzed)} posts analisados hoje, gere um resumo executivo. Responda APENAS em JSON válido:

Score médio: {avg_score}
Melhor post: {best.get('caption','')[:100]}
Alertas: {len(alerts)}
Padrões identificados: {'; '.join(padroes[:5])}

JSON esperado:
{{
  "padroes": ["<padrão 1>", "<padrão 2>", "<padrão 3>"],
  "recomendacao_amanha": "<recomendação de horário e tipo de conteúdo para amanhã>",
  "insight_principal": "<o insight mais importante do dia em 2 frases>"
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
                'max_tokens': 400,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=30
        )
        r.raise_for_status()
        text = r.json()['content'][0]['text'].strip()
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        summary = json.loads(text.strip())
        summary['score_medio'] = avg_score
        summary['total_posts'] = len(posts_analyzed)
        summary['total_alertas'] = len(alerts)
        summary['melhor_post_id'] = best.get('id','')
        summary['melhor_post_caption'] = best.get('caption','')[:100]
        summary['melhor_post_views'] = best.get('views', 0)
        return summary
    except Exception as e:
        print(f"Erro no resumo: {e}", file=sys.stderr)
        return {
            "score_medio": avg_score, "total_posts": len(posts_analyzed),
            "total_alertas": len(alerts), "padroes": padroes[:3],
            "recomendacao_amanha": "—", "insight_principal": "—",
            "melhor_post_id": best.get('id',''),
            "melhor_post_caption": best.get('caption','')[:100],
            "melhor_post_views": best.get('views', 0)
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

    date = get_yesterday()
    print(f"Analisando posts de {date}...")

    posts = get_posts_yesterday(metrics, date)
    if not posts:
        print(f"Nenhum post encontrado para {date}")
        # Salvar insights vazio mas válido
        insights = {
            "generated_at": datetime.utcnow().isoformat(),
            "date": date,
            "summary": {"total_posts": 0},
            "posts": []
        }
        with open(INSIGHTS_PATH, 'w') as f:
            json.dump(insights, f, ensure_ascii=False, indent=2)
        return

    print(f"  {len(posts)} posts encontrados")
    posts_analyzed = []
    for i, post in enumerate(posts):
        print(f"  Analisando {i+1}/{len(posts)}: {post.get('caption','')[:50]}...")
        analysis = analyze_post(post)
        posts_analyzed.append({**post, 'analysis': analysis})

    print("Gerando resumo executivo...")
    summary = generate_summary(posts_analyzed)

    insights = {
        "generated_at": datetime.utcnow().isoformat(),
        "date": date,
        "summary": summary,
        "posts": posts_analyzed
    }

    with open(INSIGHTS_PATH, 'w') as f:
        json.dump(insights, f, ensure_ascii=False, indent=2)

    print(f"Insights salvos em {INSIGHTS_PATH}")
    print(f"Score médio: {summary.get('score_medio')} | Posts: {len(posts_analyzed)} | Alertas: {summary.get('total_alertas')}")

if __name__ == '__main__':
    main()
