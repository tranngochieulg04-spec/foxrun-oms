"""A bounded subset of Sheets expressions for calculating one order's fees."""
import ast
import io
import operator
import tokenize
from decimal import Decimal, DecimalException, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, localcontext

CENT = Decimal('0.01')
MAX_FEE = Decimal('9999999999999999.99')
NAMES = {
    'cod', 'cod_thu', 'so_luong', 'so_sku', 'don', 'khach', 'tien_te', 'trang_thai',
    'true', 'false', 'bang_gia', 'ma_tra', 'phi_tra',
}
ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
COMPARISONS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
SIGNATURES = {
    'if': (2, 3), 'ifs': (2, 30), 'iferror': (1, 2), 'ifna': (1, 2),
    'and': (1, 30), 'or': (1, 30), 'not': (1, 1), 'true': (0, 0), 'false': (0, 0),
    'sum': (1, 30), 'average': (1, 30), 'count': (1, 30), 'min': (1, 30), 'max': (1, 30),
    'abs': (1, 1), 'round': (1, 2), 'roundup': (1, 2), 'rounddown': (1, 2),
    'concat': (2, 2), 'concatenate': (1, 30), 'vlookup': (4, 4), 'xlookup': (3, 6),
}


class MissingLookup(ValueError):
    pass


def normalize(source):
    """Translate Sheets operators outside quoted text before parsing."""
    source = source.strip().removeprefix('=').strip()
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    output = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        kind, value = token.type, token.string
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if kind == tokenize.COMMENT:
            raise ValueError('Công thức không hỗ trợ chú thích.')
        if kind == tokenize.NUMBER and following and following.string == '%':
            output.extend([(tokenize.OP, '('), (kind, value), (tokenize.OP, '/'),
                           (tokenize.NUMBER, '100'), (tokenize.OP, ')')])
            index += 2
            continue
        if kind == tokenize.OP and value == '<' and following and following.string == '>':
            output.append((kind, '!='))
            index += 2
            continue
        if kind == tokenize.OP:
            value = {'=': '==', '<>': '!=', ';': ','}.get(value, value)
        # AND, OR, NOT and IF may also be Python keywords in lowercase.
        if kind == tokenize.NAME and value.lower() in SIGNATURES and following and following.string == '(':
            value = 'sheet_' + value.lower()
        output.append((kind, value))
        index += 1
    return tokenize.untokenize(output).strip()


def number(value):
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, Decimal):
        return value
    raise ValueError('Phép tính này cần giá trị số.')


def truth(value):
    return number(value) != 0


def compare(left, right, op):
    if isinstance(left, str) and isinstance(right, str):
        return op(left.casefold(), right.casefold())
    if isinstance(left, str) or isinstance(right, str):
        if op in (operator.eq, operator.ne):
            return op is operator.ne
        raise ValueError('So sánh cần hai giá trị cùng kiểu.')
    return op(number(left), number(right))


def flat_values(args):
    for arg in args:
        if isinstance(arg, tuple):
            yield from flat_values(arg)
        else:
            yield arg


def lookup_key(value):
    if isinstance(value, str):
        return value.strip().casefold()
    return format(number(value).normalize(), 'f')


def lookup_position(key, keys):
    wanted = lookup_key(key)
    return next((index for index, candidate in enumerate(keys) if lookup_key(candidate) == wanted), None)


class Formula:
    def __init__(self, text):
        try:
            self.source = normalize(text)
            self.tree = ast.parse(self.source, mode='eval').body
        except (SyntaxError, tokenize.TokenError, IndentationError) as error:
            raise ValueError('Cú pháp công thức không hợp lệ.') from error
        if sum(1 for _ in ast.walk(self.tree)) > 120:
            raise ValueError('Công thức quá phức tạp.')
        self.validate(self.tree)

    def validate(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) or type(node.value) is bool:
                return
            if type(node.value) in (int, float):
                value = Decimal(ast.get_source_segment(self.source, node))
                if value.is_finite() and abs(value) <= MAX_FEE and value.as_tuple().exponent >= -12:
                    return
                raise ValueError('Số trong công thức vượt giới hạn.')
        if isinstance(node, ast.Name) and node.id.lower() in NAMES:
            return
        if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            self.validate(node.operand)
            return
        if isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC:
            self.validate(node.left)
            self.validate(node.right)
            return
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARISONS:
            self.validate(node.left)
            self.validate(node.comparators[0])
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            function = node.func.id.removeprefix('sheet_')
            if function in SIGNATURES:
                minimum, maximum = SIGNATURES[function]
                if not minimum <= len(node.args) <= maximum or function == 'ifs' and len(node.args) % 2:
                    raise ValueError('Kiểm tra số đối số của ' + function.upper() + '.')
                for arg in node.args:
                    self.validate(arg)
                return
        raise ValueError('Có toán tử, biến hoặc hàm chưa được hỗ trợ. Xem danh sách trong mục Công thức.')

    def evaluate(self, node, values):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) or type(node.value) is bool:
                return node.value
            return Decimal(ast.get_source_segment(self.source, node))
        if isinstance(node, ast.Name):
            return values[node.id.lower()]
        if isinstance(node, ast.UnaryOp):
            value = number(self.evaluate(node.operand, values))
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            return ARITHMETIC[type(node.op)](number(self.evaluate(node.left, values)), number(self.evaluate(node.right, values)))
        if isinstance(node, ast.Compare):
            return compare(self.evaluate(node.left, values), self.evaluate(node.comparators[0], values),
                           COMPARISONS[type(node.ops[0])])
        return self.call(node.func.id.removeprefix('sheet_'), node.args, values)

    def call(self, function, nodes, values):
        def evaluate(index):
            return self.evaluate(nodes[index], values)

        if function == 'if':
            if truth(evaluate(0)):
                return evaluate(1)
            return evaluate(2) if len(nodes) == 3 else Decimal(0)
        if function == 'ifs':
            for index in range(0, len(nodes), 2):
                if truth(evaluate(index)):
                    return evaluate(index + 1)
            raise MissingLookup('Không có điều kiện IFS nào đúng.')
        if function in {'iferror', 'ifna'}:
            try:
                return evaluate(0)
            except (ValueError, DecimalException) as error:
                if function == 'ifna' and not isinstance(error, MissingLookup):
                    raise
                return evaluate(1) if len(nodes) == 2 else Decimal(0)
        if function == 'true':
            return True
        if function == 'false':
            return False
        if function == 'xlookup':
            key, keys, results = (evaluate(index) for index in range(3))
            if not isinstance(keys, tuple) or not isinstance(results, tuple) or len(keys) != len(results):
                raise ValueError('XLOOKUP cần MA_TRA và PHI_TRA.')
            if (len(nodes) > 4 and number(evaluate(4)) != 0) or (len(nodes) > 5 and number(evaluate(5)) != 1):
                raise ValueError('XLOOKUP hỗ trợ khớp chính xác và tìm từ đầu bảng.')
            position = lookup_position(key, keys)
            if position is not None:
                return results[position]
            if len(nodes) >= 4:
                return evaluate(3)
            raise MissingLookup('Không tìm thấy mã tra ' + str(key) + '.')
        args = [evaluate(index) for index in range(len(nodes))]
        if function == 'vlookup':
            key, table, column, sorted_mode = args
            if not isinstance(table, tuple) or any(not isinstance(row, tuple) or len(row) != 2 for row in table):
                raise ValueError('VLOOKUP cần bảng BANG_GIA.')
            if number(column) not in (1, 2) or truth(sorted_mode):
                raise ValueError('VLOOKUP hỗ trợ cột 1 hoặc 2 và đối số FALSE để khớp chính xác.')
            position = lookup_position(key, [row[0] for row in table])
            if position is None:
                raise MissingLookup('Không tìm thấy mã tra ' + str(key) + '.')
            return table[position][int(number(column)) - 1]
        if function in {'concat', 'concatenate'}:
            return ''.join(value if isinstance(value, str) else format(number(value), 'f') for value in flat_values(args))
        if function in {'and', 'or', 'not'}:
            booleans = [truth(value) for value in flat_values(args)]
            return all(booleans) if function == 'and' else any(booleans) if function == 'or' else not booleans[0]
        if function in {'sum', 'average', 'min', 'max', 'count'}:
            numbers = [number(value) for value in flat_values(args) if isinstance(value, (Decimal, bool))]
            if function == 'count':
                return Decimal(len(numbers))
            if function == 'sum':
                return sum(numbers, Decimal(0))
            if function == 'average':
                if not numbers:
                    raise ValueError('AVERAGE cần ít nhất một số.')
                return sum(numbers) / len(numbers)
            if not numbers:
                return Decimal(0)
            return min(numbers) if function == 'min' else max(numbers)
        if function == 'abs':
            return abs(number(args[0]))
        value = number(args[0])
        digits = number(args[1]) if len(args) == 2 else Decimal(0)
        if digits != digits.to_integral_value() or not -6 <= digits <= 6:
            raise ValueError('Số chữ số làm tròn cần là số nguyên từ -6 đến 6.')
        rounding = {'round': ROUND_HALF_UP, 'roundup': ROUND_UP, 'rounddown': ROUND_DOWN}[function]
        return value.quantize(Decimal(1).scaleb(-int(digits)), rounding=rounding)

    def amount(self, values):
        with localcontext() as context:
            context.prec = 40
            result = number(self.evaluate(self.tree, values))
            if not result.is_finite() or not 0 <= result <= MAX_FEE:
                raise ValueError('Phí phải không âm và nằm trong giới hạn tiền của hệ thống.')
            return result.quantize(CENT, rounding=ROUND_HALF_UP)
