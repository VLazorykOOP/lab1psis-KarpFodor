from flask import Flask, request, render_template_string, redirect, url_for, send_from_directory
from jinja2 import Environment, FileSystemLoader
import os
import shutil
import json
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
SITE_SRC = APP_DIR / 'site_src'
STATIC_DIR = APP_DIR / 'static'
OUT_DIR = Path(os.environ.get('SITE_OUT_DIR', APP_DIR / 'site'))

app = Flask(__name__)


def load_products():
    pfile = APP_DIR / 'data' / 'products.json'
    try:
        with pfile.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_products(products):
    pfile = APP_DIR / 'data' / 'products.json'
    with pfile.open('w', encoding='utf-8') as f:
        json.dump(products, f, indent=2, ensure_ascii=False)


PAGES = [
    ('index.html', 'home.html', 'Home'),
    ('catalog.html', 'catalog.html', 'Product Catalog'),
    ('cart.html', 'cart.html', 'Cart'),
    ('contacts.html', 'contacts.html', 'Contacts'),
]


def ensure_out_dir():
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def copy_static():
    dest = OUT_DIR / 'static'
    if dest.exists():
        shutil.rmtree(dest)
    if STATIC_DIR.exists():
        shutil.copytree(STATIC_DIR, dest)


def render_pages():
    ensure_out_dir()
    env = Environment(loader=FileSystemLoader(str(SITE_SRC)))
    products = load_products()
    for out_name, tmpl_name, title in PAGES:
        tpl = env.get_template(tmpl_name)
        html = tpl.render(site_title='My Online Store', page_title=title, products=products)
        (OUT_DIR / out_name).write_text(html, encoding='utf-8')
    copy_static()


@app.route('/')
def root():
    return redirect('/index.html')


@app.route('/admin')
def admin_index():
    pages = [{'out': p[0], 'src': p[1], 'title': p[2]} for p in PAGES]
    html = ['<h1>Admin — Edit Pages</h1>', '<ul>']
    for p in pages:
        html.append(f"<li>{p['title']} - <a href='{url_for('edit_page', page=p['src'])}'>edit</a></li>")
    html.append('</ul>')
    html.append("<p><a href='/' target='_blank'>Open site root</a></p>")
    html.append("<p><a href='/api/generate'>Force regenerate</a></p>")
    return '\n'.join(html)


@app.route('/admin/edit/<path:page>', methods=['GET', 'POST'])
def edit_page(page):
    src_path = SITE_SRC / page
    if not src_path.exists():
        return f'No such page: {page}', 404
    if request.method == 'POST':
        content = request.form.get('content', '')
        src_path.write_text(content, encoding='utf-8')
        render_pages()
        return redirect(url_for('admin_index'))
    content = src_path.read_text(encoding='utf-8')
    return render_template_string('''
        <h1>Editing: {{page}}</h1>
        <form method="post">
        <textarea name="content" style="width:100%;height:60vh">{{content}}</textarea>
        <p><button type="submit">Save & Regenerate</button></p>
        </form>
        <p><a href="/admin">Back</a></p>
    ''', page=page, content=content)


@app.route('/api/generate')
def api_generate():
    try:
        render_pages()
        return {'status': 'ok', 'out': str(OUT_DIR)}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}, 500


@app.route('/api/delete_product', methods=['POST'])
def api_delete_product():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get('id'))
    except Exception:
        return {'status': 'error', 'error': 'invalid id'}, 400
    products = load_products()
    new = [p for p in products if int(p.get('id')) != pid]
    if len(new) == len(products):
        return {'status': 'error', 'error': 'not found'}, 404
    save_products(new)
    render_pages()
    return {'status': 'ok', 'id': pid}


@app.route('/api/update_product', methods=['POST'])
def api_update_product():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get('id'))
    except Exception:
        return {'status': 'error', 'error': 'invalid id'}, 400
    products = load_products()
    updated = False
    for p in products:
        if int(p.get('id')) == pid:
            if 'name' in data:
                p['name'] = data.get('name')
            if 'description' in data:
                p['description'] = data.get('description')
            if 'price' in data:
                try:
                    p['price'] = float(data.get('price'))
                except Exception:
                    pass
            if 'image' in data:
                p['image'] = data.get('image')
            updated = True
            break
    if not updated:
        return {'status': 'error', 'error': 'not found'}, 404
    save_products(products)
    render_pages()
    return {'status': 'ok', 'product': p}


@app.route('/api/add_product', methods=['POST'])
def api_add_product():
    data = request.get_json(force=True, silent=True) or {}
    products = load_products()
    try:
        new_id = max([int(p.get('id', 0)) for p in products]) + 1 if products else 1
    except Exception:
        new_id = 1
    new = {
        'id': new_id,
        'name': data.get('name', f'Product {new_id}'),
        'description': data.get('description', ''),
        'price': float(data.get('price', 0)),
        'image': data.get('image', 'placeholder.jpg')
    }
    products.append(new)
    save_products(products)
    render_pages()
    return {'status': 'ok', 'product': new}


@app.route('/site/<path:filename>')
def site_file(filename):
    return send_from_directory(str(OUT_DIR), filename)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if args.generate:
        render_pages()
        print('Generated site into', OUT_DIR)
    else:
        render_pages()
        app.run(host='0.0.0.0', port=args.port)
