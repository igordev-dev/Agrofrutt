from flask import Flask, render_template, request, redirect, url_for, Response
import sqlite3, csv, io, os
from datetime import date

app = Flask(__name__)

# Em produção (Railway) o banco fica em /data para persistir entre deploys.
# Localmente usa a pasta do projeto mesmo.
_data_dir = "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(_data_dir, "agrofrut.db")


# Abre conexão com o banco e retorna linhas como dicionário
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# Cria as tabelas na primeira execução e aplica migrações necessárias
def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS fornecedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                telefone TEXT,
                produto TEXT
            );
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                telefone TEXT
            );
            CREATE TABLE IF NOT EXISTS estoque (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto TEXT NOT NULL,
                tipo_caixa TEXT NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0,
                fornecedor_id INTEGER,
                FOREIGN KEY(fornecedor_id) REFERENCES fornecedores(id)
            );
            CREATE TABLE IF NOT EXISTS vendas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER,
                estoque_id INTEGER,
                quantidade INTEGER NOT NULL,
                valor_unitario REAL NOT NULL DEFAULT 0,
                data TEXT DEFAULT (date('now')),
                FOREIGN KEY(cliente_id) REFERENCES clientes(id),
                FOREIGN KEY(estoque_id) REFERENCES estoque(id)
            );
        """)
        # Migração: adiciona fornecedor_id ao estoque caso o banco já existia sem ela
        try:
            db.execute("ALTER TABLE estoque ADD COLUMN fornecedor_id INTEGER")
            db.commit()
        except Exception:
            pass


init_db()


# ── PÁGINA INICIAL ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    db = get_db()

    # Estoque com nome do fornecedor vinculado
    estoque = db.execute("""
        SELECT e.*, f.nome as fornecedor_nome
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """).fetchall()

    clientes    = db.execute("SELECT * FROM clientes ORDER BY nome").fetchall()
    fornecedores = db.execute("SELECT * FROM fornecedores ORDER BY nome").fetchall()

    # Faturamento total acumulado
    faturamento = db.execute(
        "SELECT COALESCE(SUM(quantidade * valor_unitario), 0) as total FROM vendas"
    ).fetchone()["total"]

    # Produto com maior volume de saída
    mais_vendido = db.execute("""
        SELECT e.produto, e.tipo_caixa, SUM(v.quantidade) as total
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        GROUP BY v.estoque_id ORDER BY total DESC LIMIT 1
    """).fetchone()

    # Itens com quantidade igual ou abaixo de 10 caixas
    estoque_baixo = db.execute(
        "SELECT * FROM estoque WHERE quantidade <= 10 ORDER BY quantidade"
    ).fetchall()

    return render_template("index.html",
        estoque=estoque, clientes=clientes, fornecedores=fornecedores,
        faturamento=faturamento, mais_vendido=mais_vendido, estoque_baixo=estoque_baixo)


# ── ESTOQUE ────────────────────────────────────────────────────────────────────

@app.route("/estoque/add", methods=["POST"])
def add_estoque():
    produto  = request.form["produto"].strip()
    tipo     = request.form["tipo_caixa"]
    qtd      = int(request.form["quantidade"])
    forn_id  = request.form.get("fornecedor_id") or None
    db = get_db()

    # Se o produto+tipo já existe, soma a quantidade em vez de duplicar
    existente = db.execute(
        "SELECT id FROM estoque WHERE produto=? AND tipo_caixa=?", (produto, tipo)
    ).fetchone()

    if existente:
        db.execute(
            "UPDATE estoque SET quantidade = quantidade + ?, fornecedor_id=? WHERE id=?",
            (qtd, forn_id, existente["id"])
        )
    else:
        db.execute(
            "INSERT INTO estoque (produto, tipo_caixa, quantidade, fornecedor_id) VALUES (?,?,?,?)",
            (produto, tipo, qtd, forn_id)
        )
    db.commit()
    return redirect(url_for("index"))


@app.route("/estoque/edit/<int:id>", methods=["POST"])
def edit_estoque(id):
    db = get_db()
    db.execute(
        "UPDATE estoque SET produto=?, tipo_caixa=?, quantidade=?, fornecedor_id=? WHERE id=?",
        (
            request.form["produto"].strip(),
            request.form["tipo_caixa"],
            int(request.form["quantidade"]),
            request.form.get("fornecedor_id") or None,
            id
        )
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/estoque/delete/<int:id>")
def del_estoque(id):
    db = get_db()
    db.execute("DELETE FROM estoque WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("index"))


# ── VENDAS ─────────────────────────────────────────────────────────────────────

@app.route("/venda/add", methods=["POST"])
def add_venda():
    cliente_id = request.form["cliente_id"]
    estoque_id = request.form["estoque_id"]
    qtd        = int(request.form["quantidade"])
    valor      = float(request.form["valor_unitario"])
    db = get_db()

    item = db.execute("SELECT quantidade FROM estoque WHERE id=?", (estoque_id,)).fetchone()

    # Bloqueia a venda se não houver estoque suficiente
    if not item or item["quantidade"] < qtd:
        return "Estoque insuficiente", 400

    # Baixa no estoque e registra a venda
    db.execute("UPDATE estoque SET quantidade = quantidade - ? WHERE id=?", (qtd, estoque_id))
    db.execute(
        "INSERT INTO vendas (cliente_id, estoque_id, quantidade, valor_unitario) VALUES (?,?,?,?)",
        (cliente_id, estoque_id, qtd, valor)
    )
    db.commit()
    return redirect(url_for("index"))


# ── CLIENTES ───────────────────────────────────────────────────────────────────

@app.route("/clientes")
def clientes():
    db = get_db()
    lista = db.execute("SELECT * FROM clientes ORDER BY nome").fetchall()

    # Últimas 50 vendas com dados do cliente e do produto
    historico = db.execute("""
        SELECT v.id, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario, v.data
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        ORDER BY v.data DESC LIMIT 50
    """).fetchall()

    return render_template("clientes.html", clientes=lista, historico=historico)


@app.route("/clientes/add", methods=["POST"])
def add_cliente():
    db = get_db()
    db.execute(
        "INSERT INTO clientes (nome, telefone) VALUES (?,?)",
        (request.form["nome"].strip(), request.form["telefone"].strip())
    )
    db.commit()
    return redirect(url_for("clientes"))


@app.route("/clientes/edit/<int:id>", methods=["POST"])
def edit_cliente(id):
    db = get_db()
    db.execute(
        "UPDATE clientes SET nome=?, telefone=? WHERE id=?",
        (request.form["nome"].strip(), request.form["telefone"].strip(), id)
    )
    db.commit()
    return redirect(url_for("clientes"))


@app.route("/clientes/delete/<int:id>")
def del_cliente(id):
    db = get_db()
    db.execute("DELETE FROM clientes WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("clientes"))


# ── FORNECEDORES ───────────────────────────────────────────────────────────────

@app.route("/fornecedores")
def fornecedores():
    db = get_db()
    lista = db.execute("SELECT * FROM fornecedores ORDER BY nome").fetchall()
    return render_template("fornecedores.html", fornecedores=lista)


@app.route("/fornecedores/add", methods=["POST"])
def add_fornecedor():
    db = get_db()
    db.execute(
        "INSERT INTO fornecedores (nome, telefone, produto) VALUES (?,?,?)",
        (request.form["nome"].strip(), request.form["telefone"].strip(), request.form["produto"].strip())
    )
    db.commit()
    return redirect(url_for("fornecedores"))


@app.route("/fornecedores/edit/<int:id>", methods=["POST"])
def edit_fornecedor(id):
    db = get_db()
    db.execute(
        "UPDATE fornecedores SET nome=?, telefone=?, produto=? WHERE id=?",
        (request.form["nome"].strip(), request.form["telefone"].strip(), request.form["produto"].strip(), id)
    )
    db.commit()
    return redirect(url_for("fornecedores"))


@app.route("/fornecedores/delete/<int:id>")
def del_fornecedor(id):
    db = get_db()
    db.execute("DELETE FROM fornecedores WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("fornecedores"))


# ── RELATÓRIO ──────────────────────────────────────────────────────────────────

def _filtro_periodo(data_ini, data_fim):
    """Monta cláusula WHERE e lista de parâmetros para filtro de período."""
    filtro, params = "", []
    if data_ini:
        filtro += " AND v.data >= ?"
        params.append(data_ini)
    if data_fim:
        filtro += " AND v.data <= ?"
        params.append(data_fim)
    return filtro, params


@app.route("/relatorio")
def relatorio():
    db = get_db()
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))
    filtro, params = _filtro_periodo(data_ini, data_fim)

    # Todas as vendas do período com dados completos
    vendas = db.execute(f"""
        SELECT v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro}
        ORDER BY v.data DESC
    """, params).fetchall()

    faturamento = sum(r["total"] for r in vendas)

    # Ranking de produtos por quantidade vendida no período
    por_produto = db.execute(f"""
        SELECT e.produto, e.tipo_caixa,
               SUM(v.quantidade) as qtd_total,
               SUM(v.quantidade * v.valor_unitario) as receita
        FROM vendas v JOIN estoque e ON v.estoque_id = e.id
        WHERE 1=1 {filtro}
        GROUP BY v.estoque_id ORDER BY qtd_total DESC
    """, params).fetchall()

    # Posição atual do estoque (sem filtro de data)
    estoque = db.execute("""
        SELECT e.produto, e.tipo_caixa, e.quantidade, f.nome as fornecedor
        FROM estoque e LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        ORDER BY e.produto
    """).fetchall()

    return render_template("relatorio.html",
        vendas=vendas, faturamento=faturamento,
        por_produto=por_produto, estoque=estoque,
        data_ini=data_ini, data_fim=data_fim)


@app.route("/relatorio/csv")
def relatorio_csv():
    db = get_db()
    data_ini = request.args.get("data_ini", "")
    data_fim = request.args.get("data_fim", str(date.today()))
    filtro, params = _filtro_periodo(data_ini, data_fim)

    vendas = db.execute(f"""
        SELECT v.data, c.nome as cliente, e.produto, e.tipo_caixa,
               v.quantidade, v.valor_unitario,
               (v.quantidade * v.valor_unitario) as total,
               f.nome as fornecedor
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        JOIN estoque e ON v.estoque_id = e.id
        LEFT JOIN fornecedores f ON e.fornecedor_id = f.id
        WHERE 1=1 {filtro}
        ORDER BY v.data DESC
    """, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Data", "Cliente", "Produto", "Tipo Caixa", "Qtd", "Valor Unit. (R$)", "Total (R$)", "Fornecedor"])
    for r in vendas:
        writer.writerow([
            r["data"], r["cliente"], r["produto"], r["tipo_caixa"],
            r["quantidade"], f'{r["valor_unitario"]:.2f}',
            f'{r["total"]:.2f}', r["fornecedor"] or ""
        ])

    output.seek(0)
    # BOM UTF-8 garante que o Excel abra com acentos corretamente
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_agrofrut.csv"}
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
