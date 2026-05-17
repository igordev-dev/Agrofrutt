
import os
from flask import Flask, render_template, request, redirect, url_for, Response, session, flash
from flask_wtf import CSRFProtect
from login_form import LoginForm
import csv, io

from datetime import date, datetime
def formatar_data_br(data_str):
    """Converte '2026-05-17' para '17-05-2026'. Aceita None ou vazio."""
    if not data_str:
        return ""
    try:
        return datetime.strptime(str(data_str), "%Y-%m-%d").strftime("%d-%m-%Y")
    except Exception:
        return str(data_str)


from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'troque-para-uma-chave-secreta')
csrf = CSRFProtect(app)

# Lê a URL do banco definida como variável de ambiente no Render.
# Em desenvolvimento local, cai para SQLite automaticamente.

DATABASE_URL = os.environ.get("DATABASE_URL")

# Usuário e senha do login
LOGIN_USER = os.environ.get('LOGIN_USER', 'admin')
LOGIN_PASSWORD = os.environ.get('LOGIN_PASSWORD', 'senha123')
def login_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapped_view

# ── PÁGINA INICIAL ─────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        if username == LOGIN_USER and password == LOGIN_PASSWORD:
            session['logged_in'] = True
            flash('Login realizado com sucesso!', 'success')
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            flash('Usuário ou senha inválidos.', 'danger')
    return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logout realizado.', 'info')
    return redirect(url_for('login'))


if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    def get_db():
        # Conexão com PostgreSQL (Supabase)
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn

    PH = "%s"
else:
    import sqlite3

    def get_db():
        # Conexão local com SQLite para desenvolvimento
        conn = sqlite3.connect("agrofrut.db")
        conn.row_factory = sqlite3.Row
        return conn

    PH = "?"


def init_db():
    """Cria as tabelas caso ainda não existam. Compatível com PostgreSQL e SQLite."""
    db = get_db()
    cur = db.cursor()

    if DATABASE_URL:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fornecedores (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                telefone TEXT,
                produto TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                telefone TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS estoque (
                id SERIAL PRIMARY KEY,
                produto TEXT NOT NULL,
                tipo_caixa TEXT NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0,
                fornecedor_id INTEGER REFERENCES fornecedores(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vendas (
                id SERIAL PRIMARY KEY,
                cliente_id INTEGER REFERENCES clientes(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario NUMERIC(10,2) NOT NULL DEFAULT 0,
                data DATE DEFAULT CURRENT_DATE
            )
        """)
        # Compras feitas junto a fornecedores — dão entrada automática no estoque
        cur.execute("""
            CREATE TABLE IF NOT EXISTS compras (
                id SERIAL PRIMARY KEY,
                fornecedor_id INTEGER REFERENCES fornecedores(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario NUMERIC(10,2) NOT NULL DEFAULT 0,
                data DATE DEFAULT CURRENT_DATE
            )
        """)
    else:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS fornecedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL, telefone TEXT, produto TEXT
            );
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL, telefone TEXT
            );
            CREATE TABLE IF NOT EXISTS estoque (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto TEXT NOT NULL, tipo_caixa TEXT NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0,
                fornecedor_id INTEGER REFERENCES fornecedores(id)
            );
            CREATE TABLE IF NOT EXISTS vendas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER REFERENCES clientes(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario REAL NOT NULL DEFAULT 0,
                data TEXT DEFAULT (date('now'))
            );
            CREATE TABLE IF NOT EXISTS compras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fornecedor_id INTEGER REFERENCES fornecedores(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario REAL NOT NULL DEFAULT 0,
                data TEXT DEFAULT (date('now'))
            );
        """)
        try:
            db.execute("ALTER TABLE estoque ADD COLUMN fornecedor_id INTEGER")
        except Exception:
            pass

    db.commit()
    if DATABASE_URL:
        cur.close()
    db.close()


init_db()


# ── HELPERS ────────────────────────────────────────────────────────────────────

def query(sql, params=()):
    """Executa SELECT e retorna lista de dicionários com valores Python nativos."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    db.close()
    result = []
    for r in rows:
        row = dict(r)
        # Converte Decimal (PostgreSQL) para float para evitar erros no template
        for k, v in row.items():
            if hasattr(v, '__class__') and v.__class__.__name__ == 'Decimal':
                row[k] = float(v)
        result.append(row)
    return result


def execute(sql, params=()):
    """Executa INSERT/UPDATE/DELETE com commit automático."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    db.commit()
    cur.close()
    db.close()


def _filtro_periodo(data_ini, data_fim, alias="v"):
    """Monta cláusula WHERE e parâmetros para filtro de período."""
    filtro, params = "", []
    if data_ini:
        filtro += f" AND {alias}.data >= {PH}"
        params.append(data_ini)
    if data_fim:
        filtro += f" AND {alias}.data <= {PH}"
        params.append(data_fim)
    return filtro, params


# ── PÁGINA INICIAL ─────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    estoque      = query(f"""
        SELECT e.*, f.nome as fornecedor_nome
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """)
    clientes     = query("SELECT * FROM clientes ORDER BY nome")
    fornecedores = query("SELECT * FROM fornecedores ORDER BY nome")

    row = query("SELECT COALESCE(SUM(quantidade * valor_unitario), 0) as total FROM vendas")
    faturamento = float(row[0]["total"])

    mv = query("""
        SELECT e.produto, e.tipo_caixa, SUM(v.quantidade) as total
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        GROUP BY e.produto, e.tipo_caixa ORDER BY total DESC LIMIT 1
    """)
    mais_vendido = mv[0] if mv else None

    estoque_baixo = query("SELECT * FROM estoque WHERE quantidade <= 10 ORDER BY quantidade")

    return render_template("index.html",
        estoque=estoque, clientes=clientes, fornecedores=fornecedores,
        faturamento=faturamento, mais_vendido=mais_vendido, estoque_baixo=estoque_baixo)


# ── ESTOQUE ────────────────────────────────────────────────────────────────────

@app.route("/estoque/add", methods=["POST"])
@login_required
def add_estoque():
    produto = request.form["produto"].strip()
    tipo    = request.form["tipo_caixa"]
    qtd     = int(request.form["quantidade"])
    forn_id = request.form.get("fornecedor_id") or None

    existente = query(
        f"SELECT id FROM estoque WHERE produto={PH} AND tipo_caixa={PH}", (produto, tipo)
    )
    if existente:
        execute(
            f"UPDATE estoque SET quantidade = quantidade + {PH}, fornecedor_id={PH} WHERE id={PH}",
            (qtd, forn_id, existente[0]["id"])
        )
    else:
        execute(
            f"INSERT INTO estoque (produto, tipo_caixa, quantidade, fornecedor_id) VALUES ({PH},{PH},{PH},{PH})",
            (produto, tipo, qtd, forn_id)
        )
    return redirect(url_for("index"))


@app.route("/estoque/edit/<int:id>", methods=["POST"])
@login_required
def edit_estoque(id):
    execute(
        f"UPDATE estoque SET produto={PH}, tipo_caixa={PH}, quantidade={PH}, fornecedor_id={PH} WHERE id={PH}",
        (request.form["produto"].strip(), request.form["tipo_caixa"],
         int(request.form["quantidade"]), request.form.get("fornecedor_id") or None, id)
    )
    return redirect(url_for("index"))


@app.route("/estoque/delete/<int:id>")
@login_required
def del_estoque(id):
    execute(f"DELETE FROM estoque WHERE id={PH}", (id,))
    return redirect(url_for("index"))


# ── VENDAS ─────────────────────────────────────────────────────────────────────

@app.route("/venda/add", methods=["POST"])
@login_required
def add_venda():
    cliente_id = request.form["cliente_id"]
    estoque_id = request.form["estoque_id"]
    qtd        = int(request.form["quantidade"])
    valor      = float(request.form["valor_unitario"])

    item = query(f"SELECT quantidade FROM estoque WHERE id={PH}", (estoque_id,))

    if not item or item[0]["quantidade"] < qtd:
        return "Estoque insuficiente", 400

    execute(f"UPDATE estoque SET quantidade = quantidade - {PH} WHERE id={PH}", (qtd, estoque_id))
    execute(
        f"INSERT INTO vendas (cliente_id, estoque_id, quantidade, valor_unitario) VALUES ({PH},{PH},{PH},{PH})",
        (cliente_id, estoque_id, qtd, valor)
    )
    return redirect(url_for("index"))


# ── CLIENTES ───────────────────────────────────────────────────────────────────

@app.route("/clientes")
@login_required
def clientes():
    lista = query("SELECT * FROM clientes ORDER BY nome")
    historico = query("""
        SELECT c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario, v.data
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        ORDER BY v.data DESC LIMIT 50
    """)
    return render_template("clientes.html", clientes=lista, historico=historico, formatar_data_br=formatar_data_br)


@app.route("/clientes/add", methods=["POST"])
@login_required
def add_cliente():
    execute(
        f"INSERT INTO clientes (nome, telefone) VALUES ({PH},{PH})",
        (request.form["nome"].strip(), request.form["telefone"].strip())
    )
    return redirect(url_for("clientes"))


@app.route("/clientes/edit/<int:id>", methods=["POST"])
@login_required
def edit_cliente(id):
    execute(
        f"UPDATE clientes SET nome={PH}, telefone={PH} WHERE id={PH}",
        (request.form["nome"].strip(), request.form["telefone"].strip(), id)
    )
    return redirect(url_for("clientes"))


@app.route("/clientes/delete/<int:id>")
@login_required
def del_cliente(id):
    execute(f"DELETE FROM clientes WHERE id={PH}", (id,))
    return redirect(url_for("clientes"))


# ── FORNECEDORES ───────────────────────────────────────────────────────────────

@app.route("/fornecedores")
@login_required
def fornecedores():
    lista    = query("SELECT * FROM fornecedores ORDER BY nome")
    estoque  = query("SELECT * FROM estoque ORDER BY produto")

    # Histórico de compras com dados completos
    compras = query("""
        SELECT c.data, f.nome as fornecedor, e.produto, e.tipo_caixa,
               c.quantidade, c.valor_unitario,
               (c.quantidade * c.valor_unitario) as total
        FROM compras c
        JOIN fornecedores f ON c.fornecedor_id = f.id
        JOIN estoque e ON c.estoque_id = e.id
        ORDER BY c.data DESC LIMIT 50
    """)
    return render_template("fornecedores.html",
        fornecedores=lista, estoque=estoque, compras=compras, formatar_data_br=formatar_data_br)


@app.route("/fornecedores/add", methods=["POST"])
@login_required
def add_fornecedor():
    execute(
        f"INSERT INTO fornecedores (nome, telefone, produto) VALUES ({PH},{PH},{PH})",
        (request.form["nome"].strip(), request.form["telefone"].strip(), request.form["produto"].strip())
    )
    return redirect(url_for("fornecedores"))


@app.route("/fornecedores/edit/<int:id>", methods=["POST"])
@login_required
def edit_fornecedor(id):
    execute(
        f"UPDATE fornecedores SET nome={PH}, telefone={PH}, produto={PH} WHERE id={PH}",
        (request.form["nome"].strip(), request.form["telefone"].strip(), request.form["produto"].strip(), id)
    )
    return redirect(url_for("fornecedores"))


@app.route("/fornecedores/delete/<int:id>")
@login_required
def del_fornecedor(id):
    execute(f"DELETE FROM fornecedores WHERE id={PH}", (id,))
    return redirect(url_for("fornecedores"))


# ── COMPRAS (entrada de mercadoria do fornecedor) ──────────────────────────────

@app.route("/compra/add", methods=["POST"])
@login_required
def add_compra():
    fornecedor_id = request.form["fornecedor_id"]
    estoque_id    = request.form["estoque_id"]
    qtd           = int(request.form["quantidade"])
    valor         = float(request.form["valor_unitario"])

    # Registra a compra
    execute(
        f"INSERT INTO compras (fornecedor_id, estoque_id, quantidade, valor_unitario) VALUES ({PH},{PH},{PH},{PH})",
        (fornecedor_id, estoque_id, qtd, valor)
    )
    # Dá entrada automática no estoque
    execute(
        f"UPDATE estoque SET quantidade = quantidade + {PH}, fornecedor_id={PH} WHERE id={PH}",
        (qtd, fornecedor_id, estoque_id)
    )
    return redirect(url_for("fornecedores"))


@app.route("/compra/delete/<int:id>")
@login_required
def del_compra(id):
    # Estorna a quantidade no estoque antes de deletar
    compra = query(f"SELECT estoque_id, quantidade FROM compras WHERE id={PH}", (id,))
    if compra:
        execute(
            f"UPDATE estoque SET quantidade = quantidade - {PH} WHERE id={PH}",
            (compra[0]["quantidade"], compra[0]["estoque_id"])
        )
    execute(f"DELETE FROM compras WHERE id={PH}", (id,))
    return redirect(url_for("fornecedores"))


# ── RELATÓRIO ──────────────────────────────────────────────────────────────────

@app.route("/relatorio")
@login_required
def relatorio():
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))

    filtro_v, params_v = _filtro_periodo(data_ini, data_fim, alias="v")
    filtro_c, params_c = _filtro_periodo(data_ini, data_fim, alias="c")

    # Vendas do período
    vendas = query(f"""
        SELECT v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro_v}
        ORDER BY v.data DESC
    """, params_v)

    faturamento = sum(float(r["total"]) for r in vendas)

    # Ranking de produtos vendidos
    por_produto = query(f"""
        SELECT e.produto, e.tipo_caixa,
               SUM(v.quantidade) as qtd_total,
               SUM(v.quantidade * v.valor_unitario) as receita
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        WHERE 1=1 {filtro_v}
        GROUP BY e.produto, e.tipo_caixa ORDER BY qtd_total DESC
    """, params_v)

    # Compras do período (custo)
    compras = query(f"""
        SELECT c.data, f.nome as fornecedor, e.produto, e.tipo_caixa,
               c.quantidade, c.valor_unitario,
               (c.quantidade * c.valor_unitario) as total
        FROM compras c
        JOIN fornecedores f ON c.fornecedor_id = f.id
        JOIN estoque e ON c.estoque_id = e.id
        WHERE 1=1 {filtro_c}
        ORDER BY c.data DESC
    """, params_c)

    custo_total = sum(float(r["total"]) for r in compras)

    # Margem bruta: receita - custo
    margem = faturamento - custo_total

    # Posição atual do estoque
    estoque = query("""
        SELECT e.produto, e.tipo_caixa, e.quantidade, f.nome as fornecedor
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """)

    return render_template("relatorio.html",
        vendas=vendas, faturamento=faturamento,
        por_produto=por_produto, estoque=estoque,
        compras=compras, custo_total=custo_total, margem=margem,
        data_ini=data_ini, data_fim=data_fim, formatar_data_br=formatar_data_br)


@app.route("/relatorio/csv")
def relatorio_csv():
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))
    filtro_v, params_v = _filtro_periodo(data_ini, data_fim, alias="v")
    filtro_c, params_c = _filtro_periodo(data_ini, data_fim, alias="c")

    vendas = query(f"""
        SELECT v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro_v}
        ORDER BY v.data DESC
    """, params_v)

    compras = query(f"""
        SELECT c.data, f.nome as fornecedor, e.produto, e.tipo_caixa,
               c.quantidade, c.valor_unitario,
               (c.quantidade * c.valor_unitario) as total
        FROM compras c
        JOIN fornecedores f ON c.fornecedor_id = f.id
        JOIN estoque e ON c.estoque_id = e.id
        WHERE 1=1 {filtro_c}
        ORDER BY c.data DESC
    """, params_c)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["=== VENDAS ==="])
    writer.writerow(["Data", "Cliente", "Produto", "Tipo Caixa", "Qtd", "Valor Unit. (R$)", "Total (R$)", "Fornecedor"])
    for r in vendas:
        writer.writerow([r["data"], r["cliente"], r["produto"], r["tipo_caixa"],
                         r["quantidade"], f'{float(r["valor_unitario"]):.2f}',
                         f'{float(r["total"]):.2f}', r["fornecedor"] or ""])

    writer.writerow([])
    writer.writerow(["=== COMPRAS ==="])
    writer.writerow(["Data", "Fornecedor", "Produto", "Tipo Caixa", "Qtd", "Valor Unit. (R$)", "Total (R$)"])
    for r in compras:
        writer.writerow([r["data"], r["fornecedor"], r["produto"], r["tipo_caixa"],
                         r["quantidade"], f'{float(r["valor_unitario"]):.2f}',
                         f'{float(r["total"]):.2f}'])

    output.seek(0)
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_agrofrut.csv"}
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
