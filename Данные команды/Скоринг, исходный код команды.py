# app/services/scoring.py
from dataclasses import dataclass
from typing import List

@dataclass
class ScoringFactor:
    name: str
    impact: int  # положительное или отрицательное влияние
    description: str

@dataclass
class ScoringResult:
    score: int  # 0-1000
    risk_level: str  # "low", "medium", "high"
    grade: str  # "A", "B", "C", "D"
    factors: List[ScoringFactor]

def calculate_scoring(application: dict, company_data: dict, bki_data: dict) -> ScoringResult:
    """
    Балльная модель скоринга для малого бизнеса
    """
    base_score = 500
    factors = []
    
    # 1. Возраст бизнеса
    age_months = company_data.get("business_age_months", 0)
    if age_months >= 24:
        factors.append(ScoringFactor("Возраст бизнеса (> 2 лет)", 25, f"{age_months} месяцев"))
    elif age_months >= 12:
        factors.append(ScoringFactor("Возраст бизнеса (1-2 года)", 10, f"{age_months} месяцев"))
    else:
        factors.append(ScoringFactor("Возраст бизнеса (< 1 года)", -50, f"{age_months} месяцев"))
    
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
    
    # 3. Долговая нагрузка (DTI)
    total_debt = company_data.get("total_debt", 0)
    dti = total_debt / max(revenue, 1)
    if dti < 0.3:
        factors.append(ScoringFactor("Низкая долговая нагрузка", 20, f"{dti*100:.0f}%"))
    elif dti < 0.6:
        factors.append(ScoringFactor("Умеренная долговая нагрузка", 0, f"{dti*100:.0f}%"))
    else:
        factors.append(ScoringFactor("Высокая долговая нагрузка", -40, f"{dti*100:.0f}%"))
    
    # 4. Просрочки в БКИ
    overdue_days = bki_data.get("max_overdue_days", 0)
    if overdue_days == 0:
        factors.append(ScoringFactor("Нет просрочек", 10, "0 дней"))
    elif overdue_days <= 30:
        factors.append(ScoringFactor("Незначительные просрочки", -20, f"{overdue_days} дней"))
    else:
        factors.append(ScoringFactor("Существенные просрочки", -50, f"{overdue_days} дней"))
    
    # 5. Количество активных кредитов
    active_loans = bki_data.get("active_loans_count", 0)
    if active_loans > 0:
        factors.append(ScoringFactor("Активные кредиты", -10 * active_loans, f"{active_loans} шт."))
    
    # 6. Налоговая задолженность
    tax_debt = company_data.get("tax_debt", 0)
    if tax_debt == 0:
        factors.append(ScoringFactor("Нет налоговой задолженности", 10, "0 ₽"))
    else:
        factors.append(ScoringFactor("Есть налоговая задолженность", -30, f"{tax_debt} ₽"))
    
    # 7. Отраслевой риск
    high_risk_okveds = ["56.10", "56.30", "41.20"]  # Рестораны, стройка
    okved = company_data.get("okved", "")
    if okved in high_risk_okveds:
        factors.append(ScoringFactor("Высокорисковая отрасль", -15, okved))
    
    # 8. Сумма кредита к выручке
    requested_amount = application.get("amount", 0)
    loan_to_revenue = requested_amount / max(revenue, 1)
    if loan_to_revenue > 0.5:
        factors.append(ScoringFactor("Сумма кредита > 50% выручки", -20, f"{loan_to_revenue*100:.0f}%"))
    
    # 9. Наличие залога
    has_collateral = application.get("has_collateral", False)
    if has_collateral:
        factors.append(ScoringFactor("Есть залог", 20, "Да"))
    
    # 10. Обороты в банке
    bank_relationship_months = company_data.get("bank_relationship_months", 0)
    if bank_relationship_months >= 12:
        factors.append(ScoringFactor("Клиент банка > 1 года", 15, f"{bank_relationship_months} мес."))
    
    # Итоговый балл
    final_score = base_score + sum(f.impact for f in factors)
    final_score = max(0, min(1000, final_score))  # Ограничение 0-1000
    
    # Уровень риска
    if final_score >= 700:
        risk_level = "low"
        grade = "A"
    elif final_score >= 500:
        risk_level = "medium"
        grade = "B" if final_score >= 600 else "C"
    else:
        risk_level = "high"
        grade = "D"
    
    return ScoringResult(
        score=final_score,
        risk_level=risk_level,
        grade=grade,
        factors=factors
    )