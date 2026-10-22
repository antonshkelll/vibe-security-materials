# app/services/scoring.py
# Балльная модель скоринга для малого бизнеса (модель команды Vibe Security).
# Скоринг только оценивает риск; решение принимают правила в decide().
from dataclasses import dataclass, field
from typing import List, Optional

SCALE_BASE = 600        # балл при нулевой сумме факторов
SCALE_MULTIPLIER = 2    # 600 + 2 * сумма: все грейды A–D достижимы
HIGH_RISK_OKVED_PREFIXES = ("56.", "41.", "42.", "43.")  # общепит и стройка

GRADE_LIMIT_SHARE = {"A": 0.40, "B": 0.30, "C": 0.20}    # лимит без залога, доля годовой выручки
GRADE_RATE_ADDON = {"A": 0.0, "B": 0.5, "C": 1.0}        # надбавка к ставке продукта, п.п.
MANUAL_REVIEW_OVER = 10_000_000
GUARANTEE_RATE = 19.5   # с поручительством Корпорации МСП
COLLATERAL_DISCOUNT = 2.0


@dataclass
class ScoringFactor:
    name: str
    impact: int          # баллы фактора по таблице команды
    description: str
    reason: Optional[str] = None  # код причины для объяснения: R01–R06

    @property
    def contribution(self) -> int:
        """Вклад в итоговый балл: на экранах показываем его, вклады складываются в балл."""
        return self.impact * SCALE_MULTIPLIER


@dataclass
class ScoringResult:
    score: int           # 0-1000
    risk_level: str      # "low", "medium", "high"
    grade: str           # "A", "B", "C", "D"
    factors: List[ScoringFactor]


@dataclass
class Offer:
    amount: int
    term_months: int
    rate: float
    kind: str            # "requested", "limit", "guarantee", "collateral"
    monthly_payment: int = 0
    overpayment: int = 0


@dataclass
class Decision:
    status: str          # APPROVED, COUNTER_OFFER, REJECTED, MANUAL_REVIEW
    reason: str
    offers: List[Offer] = field(default_factory=list)


def calculate_scoring(application: dict, company_data: dict, bki_data: dict) -> ScoringResult:
    factors = []

    # 1. Возраст бизнеса
    age_months = company_data.get("business_age_months", 0)
    if age_months >= 24:
        factors.append(ScoringFactor("Возраст бизнеса (> 2 лет)", 25, f"{age_months} месяцев"))
    elif age_months >= 12:
        factors.append(ScoringFactor("Возраст бизнеса (1-2 года)", 10, f"{age_months} месяцев"))
    else:
        factors.append(ScoringFactor("Возраст бизнеса (< 1 года)", -50, f"{age_months} месяцев", "R03"))

    # 2. Выручка
    revenue = company_data.get("annual_revenue", 0)
    if revenue >= 10_000_000:
        factors.append(ScoringFactor("Выручка (> 10 млн)", 30, f"{revenue/1_000_000:.1f} млн ₽"))
    elif revenue >= 5_000_000:
        factors.append(ScoringFactor("Выручка (5-10 млн)", 15, f"{revenue/1_000_000:.1f} млн ₽"))
    elif revenue >= 1_000_000:
        factors.append(ScoringFactor("Выручка (1-5 млн)", 0, f"{revenue/1_000_000:.1f} млн ₽"))
    else:
        factors.append(ScoringFactor("Выручка (< 1 млн)", -20, f"{revenue/1_000_000:.1f} млн ₽"))

    # 3. Долговая нагрузка: общий долг к годовой выручке
    total_debt = company_data.get("total_debt", 0)
    dti = total_debt / max(revenue, 1)
    if dti < 0.3:
        factors.append(ScoringFactor("Низкая долговая нагрузка", 20, f"{dti*100:.0f}%"))
    elif dti < 0.6:
        factors.append(ScoringFactor("Умеренная долговая нагрузка", 0, f"{dti*100:.0f}%"))
    else:
        factors.append(ScoringFactor("Высокая долговая нагрузка", -40, f"{dti*100:.0f}%", "R01"))

    # 4. Просрочки в БКИ
    overdue_days = bki_data.get("max_overdue_days", 0)
    if overdue_days == 0:
        factors.append(ScoringFactor("Нет просрочек", 10, "0 дней"))
    elif overdue_days <= 30:
        factors.append(ScoringFactor("Незначительные просрочки", -20, f"{overdue_days} дней", "R02"))
    else:
        factors.append(ScoringFactor("Существенные просрочки", -50, f"{overdue_days} дней", "R02"))

    # 5. Количество активных кредитов
    active_loans = bki_data.get("active_loans_count", 0)
    if active_loans > 0:
        factors.append(ScoringFactor("Активные кредиты", -10 * active_loans, f"{active_loans} шт.", "R05"))

    # 6. Налоговая задолженность
    tax_debt = company_data.get("tax_debt", 0)
    if tax_debt == 0:
        factors.append(ScoringFactor("Нет налоговой задолженности", 10, "0 ₽"))
    else:
        factors.append(ScoringFactor("Есть налоговая задолженность", -30, f"{tax_debt} ₽", "R04"))

    # 7. Отраслевой риск: по префиксу, чтобы попадали все коды общепита и стройки
    okved = company_data.get("okved", "")
    if okved.startswith(HIGH_RISK_OKVED_PREFIXES):
        factors.append(ScoringFactor("Высокорисковая отрасль", -15, okved))

    # 8. Сумма кредита к выручке
    requested_amount = application.get("amount", 0)
    loan_to_revenue = requested_amount / max(revenue, 1)
    if loan_to_revenue > 0.5:
        factors.append(ScoringFactor("Сумма кредита > 50% выручки", -20, f"{loan_to_revenue*100:.0f}%", "R06"))

    # 9. Наличие залога
    if application.get("has_collateral", False):
        factors.append(ScoringFactor("Есть залог", 20, "Да"))

    # 10. Клиент банка больше года
    bank_relationship_months = company_data.get("bank_relationship_months", 0)
    if bank_relationship_months >= 12:
        factors.append(ScoringFactor("Клиент банка > 1 года", 15, f"{bank_relationship_months} мес."))

    # Итоговый балл. Было 500 + сумма: максимум 630, грейд A недостижим,
    # а «Хлебный двор» с суммой −10 получал 490 и грейд D вместо C.
    points = sum(f.impact for f in factors)
    final_score = max(0, min(1000, SCALE_BASE + SCALE_MULTIPLIER * points))

    if final_score >= 700:
        risk_level, grade = "low", "A"
    elif final_score >= 500:
        risk_level, grade = "medium", ("B" if final_score >= 600 else "C")
    else:
        risk_level, grade = "high", "D"

    return ScoringResult(score=final_score, risk_level=risk_level, grade=grade, factors=factors)


def check_stop_factors(company_data: dict, bki_data: dict) -> Optional[str]:
    """Стоп-факторы проверяются до скоринга. Формулировки для клиента нейтральные."""
    if company_data.get("status", "ACTIVE") != "ACTIVE":
        return "Компания ликвидирована или находится в процессе ликвидации"
    if company_data.get("accounts_suspended", False):
        return "Операции по счетам компании приостановлены"
    if bki_data.get("max_overdue_days", 0) >= 90:
        return "В кредитной истории есть просрочка 90 дней и больше"
    return None


def annuity(amount: int, term_months: int, rate_percent: float) -> tuple:
    r = rate_percent / 100 / 12
    payment = amount * r / (1 - (1 + r) ** -term_months)
    return round(payment / 100) * 100, round((payment * term_months - amount) / 100) * 100


def decide(application: dict, company_data: dict, bki_data: dict, product_rate: float,
           sources_ok: bool = True) -> Decision:
    """Кредитная логика поверх балла."""
    stop = check_stop_factors(company_data, bki_data)
    if stop:
        return Decision("REJECTED", stop)

    amount, term = application["amount"], application["term_months"]
    if not sources_ok or amount > MANUAL_REVIEW_OVER:
        return Decision("MANUAL_REVIEW", "Нужна ручная проверка специалистом")

    result = calculate_scoring(application, company_data, bki_data)
    if result.grade == "D":
        return Decision("REJECTED", f"Грейд D, балл {result.score}")

    rate = product_rate + GRADE_RATE_ADDON[result.grade]
    limit = int(company_data["annual_revenue"] * GRADE_LIMIT_SHARE[result.grade]) // 100_000 * 100_000
    if amount <= limit:
        offer = Offer(amount, term, rate, "requested")
        offer.monthly_payment, offer.overpayment = annuity(amount, term, rate)
        return Decision("APPROVED", f"Грейд {result.grade}, сумма в пределах лимита", [offer])

    middle = (limit + amount) // 2 // 100_000 * 100_000
    offers = [
        Offer(limit, term, rate, "limit"),                                    # лимит без залога
        Offer(amount, max(term, 36), GUARANTEE_RATE, "guarantee"),            # с поручительством Корпорации МСП
        Offer(middle, term + 6, product_rate - COLLATERAL_DISCOUNT, "collateral"),  # под залог
    ]
    for o in offers:
        o.monthly_payment, o.overpayment = annuity(o.amount, o.term_months, o.rate)
    return Decision("COUNTER_OFFER", f"Сумма больше лимита грейда {result.grade}: {limit:,} ₽".replace(",", " "), offers)


if __name__ == "__main__":
    # Три профиля из моков. Ожидается: 800 A, 580 C, 200 D
    profiles = {
        "ТехноПрогресс": ({"amount": 10_000_000, "term_months": 24},
                          {"business_age_months": 103, "annual_revenue": 50_000_000, "total_debt": 5_000_000,
                           "tax_debt": 0, "okved": "62.01", "bank_relationship_months": 36},
                          {"max_overdue_days": 0, "active_loans_count": 1}),
        "Хлебный двор": ({"amount": 5_000_000, "term_months": 24},
                         {"business_age_months": 55, "annual_revenue": 15_000_000, "total_debt": 9_300_000,
                          "tax_debt": 0, "okved": "10.71", "bank_relationship_months": 24},
                         {"max_overdue_days": 3, "active_loans_count": 3}),
        "СтройМаркет": ({"amount": 3_000_000, "term_months": 24},
                        {"business_age_months": 11, "annual_revenue": 5_000_000, "total_debt": 4_000_000,
                         "tax_debt": 150_000, "okved": "41.20", "bank_relationship_months": 8},
                        {"max_overdue_days": 45, "active_loans_count": 1}),
    }
    for name, (app, company, bki) in profiles.items():
        r = calculate_scoring(app, company, bki)
        d = decide(app, company, bki, product_rate=22.0)
        print(f"{name}: {r.score} ({r.grade}) -> {d.status}")
        for o in d.offers:
            print(f"   {o.kind}: {o.amount:,} ₽ на {o.term_months} мес. под {o.rate}%: "
                  f"платёж {o.monthly_payment:,} ₽, переплата {o.overpayment:,} ₽".replace(",", " "))
