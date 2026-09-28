"""Regenerate the README architecture animation: python docs/generate_animation.py.

Documentation-only dependency: Pillow. Not required by the application.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / 'media'
OUT.mkdir(exist_ok=True)
fonts = [Path('C:/Windows/Fonts/segoeui.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
font_path = next((str(p) for p in fonts if p.exists()), None)
def font(size):
    return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)

nodes = [
    ('01', 'Observe', 'Completed candles / CSV', 'Validate timestamps and OHLC. Preserve source provenance.'),
    ('02', 'Analyze', 'Causal features + model', 'Online logistic probabilities and three adaptive strategy rules.'),
    ('03', 'Constrain', 'Risk policy + reviews', 'Limits, stale-data checks and optional hosted Jev risk review.'),
    ('04', 'Paper trade', 'BUY / SELL / HOLD', 'Simulated execution with fees, gas assumptions and slippage.'),
    ('05', 'Remember', 'Atomic SQLite checkpoint', 'Persist account state, decisions, delayed labels and incidents.'),
    ('06', 'Evaluate', 'Chronological test folds', 'Measure held-out results and compare with a trivial baseline.'),
]
frames = []
for active in range(6):
    im = Image.new('RGB', (1200, 650), '#0d1012')
    d = ImageDraw.Draw(im)
    d.text((50, 35), 'quantum-solana-trader', font=font(34), fill='#edf1ee')
    d.text((52, 87), 'LOCAL RESEARCH  /  PERSISTENT LEARNING  /  PAPER FIRST', font=font(14), fill='#9da8a2')
    d.rounded_rectangle((932, 43, 1150, 80), radius=9, outline='#c1f7a3')
    d.text((952, 51), 'MAINNET DEFAULT: OFF', font=font(14), fill='#c1f7a3')
    for i, (number, title, subtitle, detail) in enumerate(nodes):
        col, row = i % 3, i // 3
        x, y = 50 + col * 375, 153 + row * 149
        d.rounded_rectangle((x, y, x + 350, y + 119), radius=12,
                            fill='#19241b' if i == active else '#131719',
                            outline='#c1f7a3' if i == active else '#30383a', width=2)
        d.text((x + 19, y + 15), number, font=font(15), fill='#c1f7a3' if i == active else '#9da8a2')
        d.text((x + 19, y + 40), title, font=font(25), fill='#edf1ee')
        d.text((x + 19, y + 84), subtitle, font=font(15), fill='#9da8a2')
        if col < 2:
            d.text((x + 357, y + 43), '>', font=font(20), fill='#9da8a2')
    d.line((50, 452, 1150, 452), fill='#30383a', width=1)
    d.text((52, 476), nodes[active][3], font=font(20), fill='#c1f7a3')
    d.text((52, 521), 'Delayed outcomes feed learning. All model decisions remain subject to fixed risk limits.', font=font(16), fill='#9da8a2')
    d.text((52, 593), 'ARCHITECTURE ANIMATION  /  CLASSICAL ALGORITHMS  /  NO PERFORMANCE CLAIM', font=font(14), fill='#9da8a2')
    d.rectangle((50, 566, 1150, 570), fill='#30383a')
    d.rectangle((50, 566, 50 + int(1100 * (active + 1) / 6), 570), fill='#c1f7a3')
    frames.append(im)
frames[0].save(OUT / 'architecture.png')
frames[0].save(OUT / 'architecture.gif', save_all=True, append_images=frames[1:], duration=1700, loop=0, optimize=True)
print('Wrote architecture.gif and architecture.png')
