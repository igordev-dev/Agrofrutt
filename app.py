from flask import Flask, render_template, request, redirect, url_for, Response, session
import csv, io, os
from datetime import date, datetime
from functools import wraps

app = Flask(__name__)

# Chave secreta para a sessão de login
app.secret_key = os.environ.get("SECRET_KEY", "agrofrut-secret-2026")

# Credenciais definidas via variável de ambiente (padrão para desenvolvimento local)
LOGIN_USER     = os.environ.get("LOGIN_USER", "admin")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "agrofrut123")

# Lê a URL do banco definida como variável de ambiente no Render.
# Em desenvolvimento local, cai para SQLite automaticamente.
DATABASE_URL = os.environ.get("DATABASE_URL")


def login_required(f):
    """Decorator que redireciona para /login se o usuário não estiver autenticado."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def formatar_data_br(data_str):
    """Converte '2026-05-17' para '17/05/2026'. Aceita None ou vazio."""
    if not data_str:
        return ""
    try:
        return datetime.strptime(str(data_str), "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(data_str)


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
                data DATE DEFAULT CURRENT_DATE,
                pago INTEGER NOT NULL DEFAULT 0
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
                data DATE DEFAULT CURRENT_DATE,
                pago INTEGER NOT NULL DEFAULT 0
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
                data TEXT DEFAULT (date('now')),
                pago INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS compras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fornecedor_id INTEGER REFERENCES fornecedores(id),
                estoque_id INTEGER REFERENCES estoque(id),
                quantidade INTEGER NOT NULL,
                valor_unitario REAL NOT NULL DEFAULT 0,
                data TEXT DEFAULT (date('now')),
                pago INTEGER NOT NULL DEFAULT 0
            );
        """)
        try:
            db.execute("ALTER TABLE estoque ADD COLUMN fornecedor_id INTEGER")
        except Exception:
            pass

    for tabela in ("vendas", "compras"):
        try:
            cur.execute(f"ALTER TABLE {tabela} ADD COLUMN pago INTEGER NOT NULL DEFAULT 0")
            db.commit()
        except Exception:
            db.rollback()
        try:
            cur.execute(f"ALTER TABLE {tabela} ADD COLUMN anotacao TEXT DEFAULT ''")
            db.commit()
        except Exception:
            db.rollback()

    db.commit()
    if DATABASE_URL:
        cur.close()
    db.close()


init_db()


# ── LOGIN ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    if request.method == "POST":
        if (request.form["usuario"] == LOGIN_USER and
                request.form["senha"] == LOGIN_PASSWORD):
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        erro = "Usuário ou senha incorretos."
    return render_template("login.html", erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
    estoque = query("""
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
    data       = request.form.get("data")

    item = query(f"SELECT quantidade FROM estoque WHERE id={PH}", (estoque_id,))

    if not item or item[0]["quantidade"] < qtd:
        return "Estoque insuficiente", 400

    execute(f"UPDATE estoque SET quantidade = quantidade - {PH} WHERE id={PH}", (qtd, estoque_id))
    if data:
        execute(
            f"INSERT INTO vendas (cliente_id, estoque_id, quantidade, valor_unitario, data) VALUES ({PH},{PH},{PH},{PH},{PH})",
            (cliente_id, estoque_id, qtd, valor, data)
        )
    else:
        execute(
            f"INSERT INTO vendas (cliente_id, estoque_id, quantidade, valor_unitario) VALUES ({PH},{PH},{PH},{PH})",
            (cliente_id, estoque_id, qtd, valor)
        )
    return redirect(url_for("index"))


@app.route("/venda/edit/<int:id>", methods=["POST"])
@login_required
def edit_venda(id):
    nova_qtd   = int(request.form["quantidade"])
    novo_valor = float(request.form["valor_unitario"])
    tipo_caixa = request.form.get("tipo_caixa", "").strip()
    nova_data  = request.form.get("data")

    # Busca a venda original para calcular a diferença no estoque
    venda = query(f"SELECT estoque_id, quantidade FROM vendas WHERE id={PH}", (id,))
    if venda:
        diff = nova_qtd - venda[0]["quantidade"]
        execute(f"UPDATE estoque SET quantidade = quantidade - {PH} WHERE id={PH}",
                (diff, venda[0]["estoque_id"]))
        # Atualiza o tipo de caixa no estoque se informado
        if tipo_caixa:
            execute(f"UPDATE estoque SET tipo_caixa={PH} WHERE id={PH}",
                    (tipo_caixa, venda[0]["estoque_id"]))
        if nova_data:
            execute(f"UPDATE vendas SET quantidade={PH}, valor_unitario={PH}, data={PH} WHERE id={PH}",
                    (nova_qtd, novo_valor, nova_data, id))
        else:
            execute(f"UPDATE vendas SET quantidade={PH}, valor_unitario={PH} WHERE id={PH}",
                    (nova_qtd, novo_valor, id))
    return redirect(url_for("clientes"))


@app.route("/venda/delete/<int:id>")
@login_required
def del_venda(id):
    # Estorna a quantidade no estoque antes de deletar
    venda = query(f"SELECT estoque_id, quantidade FROM vendas WHERE id={PH}", (id,))
    if venda:
        execute(f"UPDATE estoque SET quantidade = quantidade + {PH} WHERE id={PH}",
                (venda[0]["quantidade"], venda[0]["estoque_id"]))
    execute(f"DELETE FROM vendas WHERE id={PH}", (id,))
    return redirect(url_for("clientes"))


@app.route("/venda/pagamento/<int:id>", methods=["POST"])
@login_required
def pagamento_venda(id):
    pago = 1 if request.form.get("pago") == "1" else 0
    execute(f"UPDATE vendas SET pago={PH} WHERE id={PH}", (pago, id))
    return redirect(url_for("clientes"))



@app.route("/clientes")
@login_required
def clientes():
    lista = query("SELECT * FROM clientes ORDER BY nome")
    historico = query("""
        SELECT v.id, c.nome as cliente, e.produto, e.tipo_caixa, v.quantidade, v.valor_unitario, v.data, v.estoque_id, v.pago
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        ORDER BY v.data DESC LIMIT 50
    """)
    estoque = query("SELECT * FROM estoque ORDER BY produto")
    return render_template("clientes.html", clientes=lista, historico=historico, estoque=estoque,
                           formatar_data_br=formatar_data_br)


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
    lista   = query("SELECT * FROM fornecedores ORDER BY nome")
    estoque = query("SELECT * FROM estoque ORDER BY produto")
    compras = query("""
        SELECT c.id, c.data, f.nome as fornecedor, e.produto, e.tipo_caixa, c.estoque_id, c.pago,
               c.quantidade, c.valor_unitario,
               (c.quantidade * c.valor_unitario) as total
        FROM compras c
        JOIN fornecedores f ON c.fornecedor_id = f.id
        JOIN estoque e ON c.estoque_id = e.id
        ORDER BY c.data DESC LIMIT 50
    """)
    return render_template("fornecedores.html",
        fornecedores=lista, estoque=estoque, compras=compras,
        formatar_data_br=formatar_data_br)


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

    # Registra a compra e dá entrada automática no estoque
    execute(
        f"INSERT INTO compras (fornecedor_id, estoque_id, quantidade, valor_unitario) VALUES ({PH},{PH},{PH},{PH})",
        (fornecedor_id, estoque_id, qtd, valor)
    )
    execute(
        f"UPDATE estoque SET quantidade = quantidade + {PH}, fornecedor_id={PH} WHERE id={PH}",
        (qtd, fornecedor_id, estoque_id)
    )
    return redirect(url_for("fornecedores"))


@app.route("/compra/edit/<int:id>", methods=["POST"])
@login_required
def edit_compra(id):
    nova_qtd   = int(request.form["quantidade"])
    novo_valor = float(request.form["valor_unitario"])

    # Busca a compra original para calcular a diferença no estoque
    compra = query(f"SELECT estoque_id, quantidade FROM compras WHERE id={PH}", (id,))
    if compra:
        diff = nova_qtd - compra[0]["quantidade"]
        # Aplica a diferença: se aumentou a compra, entra mais no estoque
        execute(f"UPDATE estoque SET quantidade = quantidade + {PH} WHERE id={PH}",
                (diff, compra[0]["estoque_id"]))
    execute(f"UPDATE compras SET quantidade={PH}, valor_unitario={PH} WHERE id={PH}",
            (nova_qtd, novo_valor, id))
    return redirect(url_for("fornecedores"))


@app.route("/compra/delete/<int:id>")
@login_required
def del_compra(id):
    # Estorna a quantidade no estoque antes de deletar o registro
    compra = query(f"SELECT estoque_id, quantidade FROM compras WHERE id={PH}", (id,))
    if compra:
        execute(
            f"UPDATE estoque SET quantidade = quantidade - {PH} WHERE id={PH}",
            (compra[0]["quantidade"], compra[0]["estoque_id"])
        )
    execute(f"DELETE FROM compras WHERE id={PH}", (id,))
    return redirect(url_for("fornecedores"))


@app.route("/venda/anotacao/<int:id>", methods=["POST"])
@login_required
def anotacao_venda(id):
    execute(f"UPDATE vendas SET anotacao={PH} WHERE id={PH}", (request.form.get("anotacao", ""), id))
    return {"status": "ok"}

@app.route("/compra/anotacao/<int:id>", methods=["POST"])
@login_required
def anotacao_compra(id):
    execute(f"UPDATE compras SET anotacao={PH} WHERE id={PH}", (request.form.get("anotacao", ""), id))
    return {"status": "ok"}

# ── RELATÓRIO ──────────────────────────────────────────────────────────────────

@app.route("/compra/pagamento/<int:id>", methods=["POST"])
@login_required
def pagamento_compra(id):
    pago = 1 if request.form.get("pago") == "1" else 0
    execute(f"UPDATE compras SET pago={PH} WHERE id={PH}", (pago, id))
    return redirect(url_for("fornecedores"))


@app.route("/relatorio")
@login_required
def relatorio():
    data_ini   = request.args.get("data_ini", "")
    data_fim   = request.args.get("data_fim", str(date.today()))
    cliente_id    = request.args.get("cliente_id", "")
    fornecedor_id = request.args.get("fornecedor_id", "")

    filtro_v, params_v = _filtro_periodo(data_ini, data_fim, alias="v")
    filtro_c, params_c = _filtro_periodo(data_ini, data_fim, alias="c")

    # Filtro adicional por cliente nas vendas
    if cliente_id:
        filtro_v += f" AND v.cliente_id = {PH}"
        params_v.append(cliente_id)

    # Filtro adicional por fornecedor nas compras
    if fornecedor_id:
        filtro_c += f" AND c.fornecedor_id = {PH}"
        params_c.append(fornecedor_id)

    todos_clientes    = query("SELECT id, nome FROM clientes ORDER BY nome")
    todos_fornecedores = query("SELECT id, nome FROM fornecedores ORDER BY nome")

    # Busca o nome do cliente filtrado para exibir no cabeçalho
    nome_cliente_filtro = ""
    if cliente_id:
        c = query(f"SELECT nome FROM clientes WHERE id={PH}", (cliente_id,))
        nome_cliente_filtro = c[0]["nome"] if c else ""

    # Busca o nome do fornecedor filtrado para exibir no cabeçalho
    nome_fornecedor_filtro = ""
    if fornecedor_id:
        f = query(f"SELECT nome FROM fornecedores WHERE id={PH}", (fornecedor_id,))
        nome_fornecedor_filtro = f[0]["nome"] if f else ""

    vendas = query(f"""
        SELECT v.id, v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               v.pago, v.anotacao,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro_v}
        ORDER BY v.data DESC
    """, params_v)

    faturamento = sum(float(r["total"]) for r in vendas if not r["pago"])

    por_produto = query(f"""
        SELECT e.produto, e.tipo_caixa,
               SUM(v.quantidade) as qtd_total,
               SUM(v.quantidade * v.valor_unitario) as receita
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        WHERE 1=1 {filtro_v}
        GROUP BY e.produto, e.tipo_caixa ORDER BY qtd_total DESC
    """, params_v)

    compras = query(f"""
        SELECT c.id, c.data, f.nome as fornecedor, e.produto, e.tipo_caixa,
               c.quantidade, c.valor_unitario,
               (c.quantidade * c.valor_unitario) as total,
               c.pago, c.anotacao
        FROM compras c
        JOIN fornecedores f ON c.fornecedor_id = f.id
        JOIN estoque e ON c.estoque_id = e.id
        WHERE 1=1 {filtro_c}
        ORDER BY c.data DESC
    """, params_c)

    custo_total = sum(float(r["total"]) for r in compras if not r["pago"])
    margem      = faturamento - custo_total

    estoque = query("""
        SELECT e.produto, e.tipo_caixa, e.quantidade, f.nome as fornecedor
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """)

    return render_template("relatorio.html",
        vendas=vendas, faturamento=faturamento,
        por_produto=por_produto, estoque=estoque,
        compras=compras, custo_total=custo_total, margem=margem,
        data_ini=data_ini, data_fim=data_fim,
        cliente_id=cliente_id, todos_clientes=todos_clientes,
        nome_cliente_filtro=nome_cliente_filtro,
        fornecedor_id=fornecedor_id, todos_fornecedores=todos_fornecedores,
        nome_fornecedor_filtro=nome_fornecedor_filtro,
        formatar_data_br=formatar_data_br)


@app.route("/relatorio/csv")
@login_required
def relatorio_csv():
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))
    filtro_v, params_v = _filtro_periodo(data_ini, data_fim, alias="v")
    filtro_c, params_c = _filtro_periodo(data_ini, data_fim, alias="c")

    vendas = query(f"""
        SELECT v.id, v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               v.pago, v.anotacao,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro_v}
        ORDER BY v.data DESC
    """, params_v)

    compras = query(f"""
        SELECT c.id, c.data, f.nome as fornecedor, e.produto, e.tipo_caixa,
               c.quantidade, c.valor_unitario,
               (c.quantidade * c.valor_unitario) as total,
               c.pago, c.anotacao
        FROM compras c
        JOIN fornecedores f ON c.fornecedor_id = f.id
        JOIN estoque e ON c.estoque_id = e.id
        WHERE 1=1 {filtro_c}
        ORDER BY c.data DESC
    """, params_c)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["=== VENDAS ==="])
    writer.writerow(["Data", "Cliente", "Produto", "Tipo Caixa", "Qtd", "Valor Unit. (R$)", "Total (R$)", "Fornecedor", "Pagamento", "Anotacao"])
    for r in vendas:
        writer.writerow([r["data"], r["cliente"], r["produto"], r["tipo_caixa"],
                         r["quantidade"], f'{float(r["valor_unitario"]):.2f}',
                         f'{float(r["total"]):.2f}', r["fornecedor"] or "", "Sim" if r["pago"] else "", r["anotacao"] or ""])

    writer.writerow([])
    writer.writerow(["=== COMPRAS ==="])
    writer.writerow(["Data", "Fornecedor", "Produto", "Tipo Caixa", "Qtd", "Valor Unit. (R$)", "Total (R$)", "Pagamento", "Anotacao"])
    for r in compras:
        writer.writerow([r["data"], r["fornecedor"], r["produto"], r["tipo_caixa"],
                         r["quantidade"], f'{float(r["valor_unitario"]):.2f}',
                         f'{float(r["total"]):.2f}', "Sim" if r["pago"] else "", r["anotacao"] or ""])

    output.seek(0)
    # BOM UTF-8 garante que o Excel abra com acentos corretamente
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_agrofrutt.csv"}
    )


# Propaga exceções para mostrar o erro real no navegador em vez de página genérica
app.config["PROPAGATE_EXCEPTIONS"] = True

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
