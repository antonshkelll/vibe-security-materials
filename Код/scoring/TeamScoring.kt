package com.alfabank.credit.scoring

import java.math.BigDecimal
import java.math.MathContext
import java.math.RoundingMode

/**
 * Модель скоринга команды: 10 факторов, балл 0–1000, грейды A–D.
 * Перенос scoring.py на Kotlin. Скоринг только оценивает риск, решение принимает decide().
 * Данные приходят из мок-сервисов (см. mocks/README.md, таблица «Как бэкенд берёт поля»).
 */
data class ScoringInput(
    val businessAgeMonths: Int,
    val annualRevenue: BigDecimal,
    val totalDebt: BigDecimal,
    val taxDebt: BigDecimal,
    val okved: String,
    val bankRelationshipMonths: Int,
    val maxOverdueDays: Int,
    val activeLoansCount: Int,
    val requestedAmount: BigDecimal,
    val termMonths: Int,
    val hasCollateral: Boolean,
    val companyStatus: String = "ACTIVE",
    val accountsSuspended: Boolean = false,
)

data class ScoreFactor(
    val name: String,
    val points: Int,
    val description: String,
    val reasonCode: String? = null,
) {
    /** Вклад в итоговый балл: показываем на экранах, вклады складываются в балл. */
    val contribution: Int get() = points * TeamScoring.SCALE_MULTIPLIER
}

data class TeamScoringResult(
    val score: Int,
    val grade: String,
    val riskLevel: String,
    val factors: List<ScoreFactor>,
)

data class OfferOption(
    val kind: String, // REQUESTED, LIMIT, GUARANTEE, COLLATERAL
    val amount: BigDecimal,
    val termMonths: Int,
    val ratePercent: BigDecimal,
    val monthlyPayment: BigDecimal,
    val overpayment: BigDecimal,
)

data class DecisionOutcome(
    val status: String, // APPROVED, COUNTER_OFFER, REJECTED, MANUAL_REVIEW
    val reason: String,
    val scoring: TeamScoringResult? = null,
    val offers: List<OfferOption> = emptyList(),
)

object TeamScoring {
    const val SCALE_BASE = 600
    const val SCALE_MULTIPLIER = 2
    private val HIGH_RISK_OKVED = listOf("56.", "41.", "42.", "43.")
    private val LIMIT_SHARE = mapOf("A" to BigDecimal("0.40"), "B" to BigDecimal("0.30"), "C" to BigDecimal("0.20"))
    private val RATE_ADDON = mapOf("A" to BigDecimal("0.0"), "B" to BigDecimal("0.5"), "C" to BigDecimal("1.0"))
    private val MANUAL_REVIEW_OVER = BigDecimal("10000000")
    private val GUARANTEE_RATE = BigDecimal("19.5")
    private val COLLATERAL_DISCOUNT = BigDecimal("2.0")
    private val STEP = BigDecimal("100000")
    private val MC = MathContext.DECIMAL64

    fun score(input: ScoringInput): TeamScoringResult {
        val f = mutableListOf<ScoreFactor>()
        val revenue = input.annualRevenue
        val revenueMln = revenue.divide(BigDecimal("1000000"), 1, RoundingMode.HALF_UP)

        // 1. Возраст бизнеса
        when {
            input.businessAgeMonths >= 24 -> f += ScoreFactor("Возраст бизнеса больше 2 лет", 25, "${input.businessAgeMonths} мес.")
            input.businessAgeMonths >= 12 -> f += ScoreFactor("Возраст бизнеса 1–2 года", 10, "${input.businessAgeMonths} мес.")
            else -> f += ScoreFactor("Бизнесу меньше года", -50, "${input.businessAgeMonths} мес.", "R03")
        }
        // 2. Выручка
        when {
            revenue >= BigDecimal("10000000") -> f += ScoreFactor("Выручка больше 10 млн", 30, "$revenueMln млн ₽")
            revenue >= BigDecimal("5000000") -> f += ScoreFactor("Выручка 5–10 млн", 15, "$revenueMln млн ₽")
            revenue >= BigDecimal("1000000") -> f += ScoreFactor("Выручка 1–5 млн", 0, "$revenueMln млн ₽")
            else -> f += ScoreFactor("Выручка меньше 1 млн", -20, "$revenueMln млн ₽")
        }
        // 3. Долговая нагрузка: общий долг к годовой выручке
        val dti = input.totalDebt.divide(revenue.max(BigDecimal.ONE), MC)
        val dtiPct = "${dti.multiply(BigDecimal(100)).setScale(0, RoundingMode.HALF_UP)}%"
        when {
            dti < BigDecimal("0.3") -> f += ScoreFactor("Низкая долговая нагрузка", 20, dtiPct)
            dti < BigDecimal("0.6") -> f += ScoreFactor("Умеренная долговая нагрузка", 0, dtiPct)
            else -> f += ScoreFactor("Высокая долговая нагрузка", -40, dtiPct, "R01")
        }
        // 4. Просрочки в БКИ
        when {
            input.maxOverdueDays == 0 -> f += ScoreFactor("Нет просрочек", 10, "0 дней")
            input.maxOverdueDays <= 30 -> f += ScoreFactor("Незначительные просрочки", -20, "${input.maxOverdueDays} дн.", "R02")
            else -> f += ScoreFactor("Существенные просрочки", -50, "${input.maxOverdueDays} дн.", "R02")
        }
        // 5. Количество действующих кредитов
        if (input.activeLoansCount > 0) {
            f += ScoreFactor("Действующие кредиты", -10 * input.activeLoansCount, "${input.activeLoansCount} шт.", "R05")
        }
        // 6. Налоговая задолженность
        if (input.taxDebt.signum() == 0) f += ScoreFactor("Нет налоговой задолженности", 10, "0 ₽")
        else f += ScoreFactor("Есть налоговая задолженность", -30, "${input.taxDebt.toPlainString()} ₽", "R04")
        // 7. Отраслевой риск: общепит и стройка по префиксу ОКВЭД
        if (HIGH_RISK_OKVED.any { input.okved.startsWith(it) }) f += ScoreFactor("Высокорисковая отрасль", -15, input.okved)
        // 8. Сумма кредита к выручке
        val loanToRevenue = input.requestedAmount.divide(revenue.max(BigDecimal.ONE), MC)
        if (loanToRevenue > BigDecimal("0.5")) {
            f += ScoreFactor("Сумма кредита больше 50% выручки", -20,
                "${loanToRevenue.multiply(BigDecimal(100)).setScale(0, RoundingMode.HALF_UP)}%", "R06")
        }
        // 9. Залог
        if (input.hasCollateral) f += ScoreFactor("Есть залог", 20, "да")
        // 10. Клиент банка больше года
        if (input.bankRelationshipMonths >= 12) f += ScoreFactor("Клиент банка больше года", 15, "${input.bankRelationshipMonths} мес.")

        val score = (SCALE_BASE + SCALE_MULTIPLIER * f.sumOf { it.points }).coerceIn(0, 1000)
        val grade = when {
            score >= 700 -> "A"
            score >= 600 -> "B"
            score >= 500 -> "C"
            else -> "D"
        }
        val risk = when (grade) { "A" -> "low"; "D" -> "high"; else -> "medium" }
        return TeamScoringResult(score, grade, risk, f)
    }

    /** Стоп-факторы до скоринга; тексты для клиента нейтральные. */
    fun stopFactor(input: ScoringInput): String? = when {
        input.companyStatus != "ACTIVE" -> "Компания ликвидирована или находится в процессе ликвидации"
        input.accountsSuspended -> "Операции по счетам компании приостановлены"
        input.maxOverdueDays >= 90 -> "В кредитной истории есть просрочка 90 дней и больше"
        else -> null
    }

    fun decide(input: ScoringInput, productRatePercent: BigDecimal, sourcesOk: Boolean = true): DecisionOutcome {
        stopFactor(input)?.let { return DecisionOutcome("REJECTED", it) }
        if (!sourcesOk || input.requestedAmount > MANUAL_REVIEW_OVER) {
            return DecisionOutcome("MANUAL_REVIEW", "Нужна ручная проверка специалистом")
        }
        val result = score(input)
        if (result.grade == "D") return DecisionOutcome("REJECTED", "Грейд D, балл ${result.score}", result)

        val rate = productRatePercent + RATE_ADDON.getValue(result.grade)
        val limit = roundDown(input.annualRevenue.multiply(LIMIT_SHARE.getValue(result.grade)))
        val amount = input.requestedAmount
        if (amount <= limit) {
            return DecisionOutcome("APPROVED", "Грейд ${result.grade}, сумма в пределах лимита", result,
                listOf(offer("REQUESTED", amount, input.termMonths, rate)))
        }
        val middle = roundDown(limit.add(amount).divide(BigDecimal(2), MC))
        val offers = listOf(
            offer("LIMIT", limit, input.termMonths, rate),
            offer("GUARANTEE", amount, maxOf(input.termMonths, 36), GUARANTEE_RATE),
            offer("COLLATERAL", middle, input.termMonths + 6, productRatePercent - COLLATERAL_DISCOUNT),
        )
        return DecisionOutcome("COUNTER_OFFER", "Сумма больше лимита грейда ${result.grade}: ${limit.toPlainString()} ₽", result, offers)
    }

    private fun roundDown(x: BigDecimal): BigDecimal = x.divide(STEP, 0, RoundingMode.DOWN).multiply(STEP)

    private fun offer(kind: String, amount: BigDecimal, term: Int, ratePercent: BigDecimal): OfferOption {
        val r = ratePercent.divide(BigDecimal(1200), MC)
        val factor = BigDecimal.ONE.add(r).pow(term, MC)
        val payment = amount.multiply(r).multiply(factor).divide(factor.subtract(BigDecimal.ONE), MC)
        val overpay = payment.multiply(BigDecimal(term)).subtract(amount)
        return OfferOption(kind, amount, term, ratePercent, round100(payment), round100(overpay))
    }

    private fun round100(x: BigDecimal): BigDecimal = x.divide(BigDecimal(100), 0, RoundingMode.HALF_UP).multiply(BigDecimal(100))
}
