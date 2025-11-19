from flask import Flask, request, render_template_string, redirect, url_for, send_from_directory, session
from jinja2 import Environment, FileSystemLoader
import os
import shutil
import json
from pathlib import Path
import time
import uuid
import threading

APP_DIR = Path(__file__).parent.resolve()
SITE_SRC = APP_DIR / 'site_src'
STATIC_DIR = APP_DIR / 'static'
OUT_DIR = Path(os.environ.get('SITE_OUT_DIR', APP_DIR / 'site'))

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET') or str(uuid.uuid4())


DB_HOST = os.environ.get('DB_HOST', 'db')
DB_PORT = int(os.environ.get('DB_PORT', 5432))
DB_NAME = os.environ.get('DB_NAME', 'demo_store')
DB_USER = os.environ.get('DB_USER', 'demo_user')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'demo_pass')

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PG = True
except Exception:
    _HAS_PG = False


def _get_db_conn():
    if not _HAS_PG:
        raise RuntimeError('psycopg2 not installed')
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)


def init_db():
    if not _HAS_PG:
        return False
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                name TEXT,
                description TEXT,
                price NUMERIC,
                image TEXT
            )
        ''')
        conn.commit()
        cur.execute('SELECT COUNT(1) FROM products')
        cnt = cur.fetchone()[0]
        if cnt == 0:
            pfile = APP_DIR / 'data' / 'products.json'
            try:
                with pfile.open('r', encoding='utf-8') as f:
                    initial = json.load(f)
            except Exception:
                initial = []
            if initial:
                for p in initial:
                    try:
                        pid = int(p.get('id') or 0)
                    except Exception:
                        pid = None
                    name = p.get('name') or ''
                    desc = p.get('description') or ''
                    price = p.get('price') or 0
                    image = p.get('image') or ''
                    if pid:
                        cur.execute('INSERT INTO products (id, name, description, price, image) VALUES (%s, %s, %s, %s, %s)', (pid, name, desc, price, image))
                    else:
                        cur.execute('INSERT INTO products (name, description, price, image) VALUES (%s, %s, %s, %s)', (name, desc, price, image))
                conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def load_products():
    if _HAS_PG:
        try:
            conn = _get_db_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT id, name, description, price, image FROM products ORDER BY id')
            rows = cur.fetchall()
            cur.close()
            conn.close()
            out = []
            for r in rows:
                item = dict(r)
                try:
                    item['id'] = int(item.get('id'))
                except Exception:
                    item['id'] = None
                try:
                    item['price'] = float(item.get('price') or 0)
                except Exception:
                    item['price'] = 0.0
                out.append(item)
            return out
        except Exception:
            pass
    pfile = APP_DIR / 'data' / 'products.json'
    try:
        with pfile.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_products(products):
    if _HAS_PG:
        try:
            conn = _get_db_conn()
            cur = conn.cursor()
            cur.execute('BEGIN')
            cur.execute('DELETE FROM products')
            for p in products:
                pid = int(p.get('id') or 0)
                name = p.get('name') or ''
                desc = p.get('description') or ''
                price = p.get('price') or 0
                image = p.get('image') or ''
                cur.execute('INSERT INTO products (id, name, description, price, image) VALUES (%s, %s, %s, %s, %s)', (pid, name, desc, price, image))
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    pfile = APP_DIR / 'data' / 'products.json'
    with pfile.open('w', encoding='utf-8') as f:
        json.dump(products, f, indent=2, ensure_ascii=False)


PAGES = [
    ('index.html', 'home.html', 'Home'),
    ('catalog.html', 'catalog.html', 'Product Catalog'),
    ('cart.html', 'cart.html', 'Cart'),
    ('contacts.html', 'contacts.html', 'Contacts'),
]

# allowed src templates
SRC_FILES = {p[1] for p in PAGES}

# locks stored as small json files in site_src/.locks
LOCK_DIR = SITE_SRC / '.locks'
LOCK_TTL = int(os.environ.get('PAGE_LOCK_TTL', 300))
LOCK_RENEW = int(os.environ.get('PAGE_LOCK_RENEW', 60))



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


def _safe_src_path(page_name: str) -> Path:
    if page_name not in SRC_FILES:
        raise ValueError('invalid page')
    return SITE_SRC / page_name


def _lock_path(page_name: str) -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    safe = page_name.replace('/', '_')
    return LOCK_DIR / (safe + '.lock')


def _now():
    return int(time.time())


def get_lock(page_name: str):
    lp = _lock_path(page_name)
    if not lp.exists():
        return None
    try:
        data = json.loads(lp.read_text(encoding='utf-8'))
    except Exception:
        return None
    if data.get('expires_at', 0) < _now():
        try:
            lp.unlink()
        except Exception:
            pass
        return None
    return data


def acquire_lock(page_name: str, owner_id: str, owner_name: str = None):
    lp = _lock_path(page_name)
    cur = get_lock(page_name)
    if cur is None or cur.get('owner') == owner_id:
        data = {'owner': owner_id, 'owner_name': owner_name or '', 'acquired_at': _now(), 'expires_at': _now() + LOCK_TTL}
        lp.write_text(json.dumps(data), encoding='utf-8')
        return data
    return None


def release_lock(page_name: str, owner_id: str):
    lp = _lock_path(page_name)
    cur = get_lock(page_name)
    if cur and cur.get('owner') == owner_id:
        try:
            lp.unlink()
        except Exception:
            pass
        return True
    return False


def renew_lock(page_name: str, owner_id: str):
    cur = get_lock(page_name)
    if cur and cur.get('owner') == owner_id:
        cur['expires_at'] = _now() + LOCK_TTL
        lp = _lock_path(page_name)
        lp.write_text(json.dumps(cur), encoding='utf-8')
        return cur
    return None


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
        try:
                src_path = _safe_src_path(page)
        except ValueError:
                return f'No such page: {page}', 404

        # ensure editor id in session
        if 'editor_id' not in session:
                session['editor_id'] = str(uuid.uuid4())

        if request.method == 'POST':
                # only allow save if session owns the lock
                lock = get_lock(page)
                if not lock or lock.get('owner') != session.get('editor_id'):
                        return 'Page is locked by another editor. Acquire lock before saving.', 403
                content = request.form.get('content', '')
                src_path.write_text(content, encoding='utf-8')
                # release lock after save
                try:
                        release_lock(page, session.get('editor_id'))
                except Exception:
                        pass
                render_pages()
                return redirect(url_for('admin_index'))

        content = src_path.read_text(encoding='utf-8')
        return render_template_string('''
                <h1>Editing: {{page}}</h1>
                <div id="lock-status" style="margin-bottom:0.5rem;color:#444">Lock: <span id="lock-text">checking...</span></div>
                <form method="post" id="edit-form">
                <textarea id="content" name="content" style="width:100%;height:60vh" disabled>{{content}}</textarea>
                <p><button type="submit" id="save-btn" disabled>Save & Regenerate</button>
                <button type="button" id="release-btn" style="margin-left:1rem;display:none">Release Lock</button></p>
                </form>
                <p><a href="/admin">Back</a></p>
                <script>
                const PAGE = {{page|tojson}};
                async function jsonPost(url, data){
                    const r = await fetch(url, {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(data)});
                    return r.ok ? r.json() : Promise.reject(await r.text());
                }

                let keepAlive = null;
                async function acquire(){
                    try{
                        const res = await jsonPost('/api/lock_acquire', {page: PAGE});
                        if(res && res.status === 'ok'){
                            document.getElementById('lock-text').innerText = 'locked by you (expires ' + new Date(res.expires_at*1000).toLocaleTimeString() + ')';
                            document.getElementById('content').disabled = false;
                            document.getElementById('save-btn').disabled = false;
                            document.getElementById('release-btn').style.display = 'inline-block';
                            keepAlive = setInterval(()=>fetch('/api/lock_keepalive', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({page: PAGE})} ), {{interval}});
                        } else {
                            document.getElementById('lock-text').innerText = 'locked by another user';
                        }
                    }catch(e){
                        document.getElementById('lock-text').innerText = 'error: '+e;
                    }
                }

                async function release(){
                    try{ await jsonPost('/api/lock_release', {page: PAGE}); }catch(e){ }
                    if(keepAlive) clearInterval(keepAlive);
                    document.getElementById('lock-text').innerText = 'released';
                    document.getElementById('content').disabled = true;
                    document.getElementById('save-btn').disabled = true;
                    document.getElementById('release-btn').style.display = 'none';
                }

                document.getElementById('release-btn').addEventListener('click', release);
                window.addEventListener('beforeunload', ()=>{ navigator.sendBeacon('/api/lock_release', JSON.stringify({page: PAGE})); });
                (async ()=>{ await acquire(); })();
                </script>
        ''', page=page, content=content, interval=LOCK_RENEW*1000)


@app.route('/api/generate')
def api_generate():
    try:
        render_pages()
        return {'status': 'ok', 'out': str(OUT_DIR)}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}, 500


@app.route('/api/lock_acquire', methods=['POST'])
def api_lock_acquire():
    data = request.get_json(force=True, silent=True) or {}
    page = data.get('page')
    if not page:
        return {'status': 'error', 'error': 'missing page'}, 400
    try:
        _safe_src_path(page)
    except Exception:
        return {'status': 'error', 'error': 'invalid page'}, 400
    if 'editor_id' not in session:
        session['editor_id'] = str(uuid.uuid4())
    owner = session.get('editor_id')
    owner_name = data.get('owner_name') or ''
    got = acquire_lock(page, owner, owner_name)
    if got:
        return {'status': 'ok', 'owner': owner, 'acquired_at': got['acquired_at'], 'expires_at': got['expires_at']}
    else:
        cur = get_lock(page)
        return {'status': 'locked', 'owner': cur.get('owner'), 'owner_name': cur.get('owner_name'), 'expires_at': cur.get('expires_at')}, 409


@app.route('/api/lock_release', methods=['POST'])
def api_lock_release():
    data = request.get_json(force=True, silent=True) or {}
    page = data.get('page')
    if not page:
        return {'status': 'error', 'error': 'missing page'}, 400
    try:
        _safe_src_path(page)
    except Exception:
        return {'status': 'error', 'error': 'invalid page'}, 400
    if 'editor_id' not in session:
        session['editor_id'] = str(uuid.uuid4())
    owner = session.get('editor_id')
    ok = release_lock(page, owner)
    if ok:
        return {'status': 'ok'}
    return {'status': 'error', 'error': 'not owner or not locked'}, 403


@app.route('/api/lock_keepalive', methods=['POST'])
def api_lock_keepalive():
    data = request.get_json(force=True, silent=True) or {}
    page = data.get('page')
    if not page:
        return {'status': 'error', 'error': 'missing page'}, 400
    try:
        _safe_src_path(page)
    except Exception:
        return {'status': 'error', 'error': 'invalid page'}, 400
    if 'editor_id' not in session:
        session['editor_id'] = str(uuid.uuid4())
    owner = session.get('editor_id')
    renewed = renew_lock(page, owner)
    if renewed:
        return {'status': 'ok', 'expires_at': renewed['expires_at']}
    return {'status': 'error', 'error': 'not owner or not locked'}, 403


@app.route('/api/cart_add', methods=['POST'])
def api_cart_add():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get('id'))
    except Exception:
        return {'status': 'error', 'error': 'invalid id'}, 400
    qty = int(data.get('qty', 1)) if data.get('qty') is not None else 1
    products = load_products()
    if not any(int(p.get('id')) == pid for p in products):
        return {'status': 'error', 'error': 'not found'}, 404
    cart = session.get('cart', {})
    key = str(pid)
    cart[key] = int(cart.get(key, 0)) + max(1, qty)
    session['cart'] = cart
    return {'status': 'ok', 'cart': cart}


@app.route('/api/cart_get', methods=['GET'])
def api_cart_get():
    cart = session.get('cart', {})
    products = load_products()
    items = []
    for pid_str, q in cart.items():
        try:
            pid = int(pid_str)
        except Exception:
            continue
        for p in products:
            if int(p.get('id')) == pid:
                item = p.copy()
                item['qty'] = int(q)
                items.append(item)
                break
    total = sum(float(i.get('price', 0)) * int(i.get('qty', 1)) for i in items)
    return {'status': 'ok', 'items': items, 'total': total}


@app.route('/api/cart_remove', methods=['POST'])
def api_cart_remove():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get('id'))
    except Exception:
        return {'status': 'error', 'error': 'invalid id'}, 400
    cart = session.get('cart', {})
    key = str(pid)
    if key in cart:
        del cart[key]
        session['cart'] = cart
        return {'status': 'ok', 'cart': cart}
    return {'status': 'error', 'error': 'not in cart'}, 404


@app.route('/api/cart_update', methods=['POST'])
def api_cart_update():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get('id'))
        qty = int(data.get('qty'))
    except Exception:
        return {'status': 'error', 'error': 'invalid input'}, 400
    if qty < 1:
        return {'status': 'error', 'error': 'qty must be >=1'}, 400
    cart = session.get('cart', {})
    key = str(pid)
    cart[key] = qty
    session['cart'] = cart
    return {'status': 'ok', 'cart': cart}


@app.route('/api/cart_clear', methods=['POST'])
def api_cart_clear():
    session['cart'] = {}
    return {'status': 'ok'}


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
        init_db()
        render_pages()
        print('Generated site into', OUT_DIR)
    else:

        init_db()
        render_pages()

        debug_mode = os.environ.get('FLASK_ENV') == 'development'
        is_reloader_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


        def start_watcher_if_needed():
            def file_mtimes(roots):
                mt = {}
                for root in roots:
                    if not root.exists():
                        continue
                    for dirpath, _, filenames in os.walk(str(root)):
                        for fn in filenames:
                            try:
                                p = Path(dirpath) / fn
                                mt[str(p)] = p.stat().st_mtime
                            except Exception:
                                continue
                return mt

            def watcher_loop(interval=1.0):
                watch_roots = [SITE_SRC, STATIC_DIR, APP_DIR / 'data']
                prev = file_mtimes(watch_roots)
                while True:
                    time.sleep(interval)
                    cur = file_mtimes(watch_roots)
                    if cur != prev:
                        try:
                            print('Detected source change, regenerating site...')
                            render_pages()
                        except Exception as e:
                            print('Error rendering pages:', e)
                        prev = cur

            t = threading.Thread(target=watcher_loop, args=(1.0,), daemon=True)
            t.start()

        if not debug_mode or is_reloader_child:
            start_watcher_if_needed()

        app.run(host='0.0.0.0', port=args.port, debug=debug_mode, use_reloader=debug_mode)
