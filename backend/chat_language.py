"""Small deterministic language and consent rules for server-owned chat actions."""

import re
import unicodedata
from decimal import Decimal


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"\s+", " ", re.sub(r"[,!.]+", " ", text)).strip()


CONFIRMATIONS = {
    "да добавь", "подтверждаю добавление", "да добавьте",
    "иә қос", "иә қосыңыз", "иә себетке қос", "иә себетке қосыңыз",
    "иа кос", "иа косыныз", "қосуды растаймын",
    "yes add", "yes add it", "yes add to cart", "confirm addition",
    "иә добавь", "да қос", "да қосыңыз", "yes добавь", "да add", "yes қос",
}
CANCELLATIONS = {"нет не добавляй", "отмени предложение", "жоқ қоспа", "жоқ қоспаңыз",
                 "ұсынысты тоқтат", "no don't add", "cancel proposal", "жоқ не добавляй"}


def action(text: str) -> str | None:
    value = normalized(text)
    if value in CONFIRMATIONS:
        return "confirm"
    if value in CANCELLATIONS:
        return "cancel"
    return None


def language_request(text: str) -> str | None:
    value = normalized(text)
    if value in {"қазақша", "kazakh", "english", "русский"}:
        return {"қазақша": "kk", "kazakh": "kk", "english": "en", "русский": "ru"}[value]
    for language, phrases in {
        "kk": ("қазақша жауап", "қазақ тілінде", "қазақша сөйл", "на казахском", "in kazakh"),
        "en": ("answer in english", "in english please", "english please", "на английском", "ағылшынша"),
        "ru": ("на русском", "по-русски", "орысша", "орыс тілінде", "in russian"),
    }.items():
        if any(phrase in value for phrase in phrases):
            return language
    return None


def select_language(text: str, explicit: str | None = None, preferred: str | None = None) -> str:
    requested = explicit or language_request(text)
    if requested:
        return requested
    if preferred:
        return preferred
    value = normalized(text)
    if re.search("[әғқңөұүһі]", value) or re.search(r"\b(маган|керек|барма|иа|косыныз)\b", value):
        return "kk"
    if re.search(r"\b(please|need|want|yes|add|delivery|payment|stock|hello|thanks|alternative)\b", value):
        return "en"
    return "ru"


MESSAGES = {
    "no_proposal": {
        "ru": "Нет действующего предложения. Выберите товар и количество заново.",
        "kk": "Растайтын белсенді ұсыныс жоқ. Тауар мен санын қайта таңдаңыз.",
        "en": "There is no active proposal. Choose the product and quantity again.",
    },
    "added": {"ru": "Товары добавлены в корзину.", "kk": "Тауарлар себетке қосылды.", "en": "The items have been added to your cart."},
    "cancelled": {"ru": "Предложение отменено. Корзина не изменилась.", "kk": "Ұсыныс тоқтатылды. Себет өзгерген жоқ.", "en": "The proposal was cancelled. Your cart is unchanged."},
    "stock": {
        "ru": "Не удалось добавить товар {product_id}: общий остаток — {available_quantity}, уже в корзине — {already_in_cart}. Можно добавить ещё не более {remaining}. Корзина не изменилась. Выберите другое количество или попросите подобрать аналог; затем потребуется новое подтверждение.",
        "kk": "{product_id} тауарын қосу мүмкін болмады: жалпы қалдық — {available_quantity}, себетте — {already_in_cart}. Ең көбі тағы {remaining} қосуға болады. Себет өзгерген жоқ. Басқа санды таңдаңыз немесе балама тауар сұраңыз; содан кейін қайта растау қажет.",
        "en": "Could not add product {product_id}: total stock is {available_quantity}, with {already_in_cart} already in your cart. You can add at most {remaining} more. Your cart is unchanged. Choose a different quantity or ask for an alternative; a new confirmation will be required.",
    },
    "quantity": {
        "ru": "Количество не соответствует шагу продажи. Корзина не изменилась. Уточните допустимое количество и подтвердите новое предложение.",
        "kk": "Тауар саны сату қадамына сәйкес емес. Себет өзгерген жоқ. Жарамды санды таңдап, жаңа ұсынысты растаңыз.",
        "en": "The quantity does not match the sale increment. Your cart is unchanged. Choose a valid quantity and confirm a new proposal.",
    },
    "price": {"ru": "Цена изменилась. Корзина не изменилась. Запросите новое предложение и подтвердите актуальную цену.",
              "kk": "Баға өзгерді. Себет өзгерген жоқ. Жаңа ұсыныс сұрап, қазіргі бағаны растаңыз.",
              "en": "The price changed. Your cart is unchanged. Request a new proposal and confirm the current price."},
}


def message(key: str, language: str, details: dict | None = None) -> str:
    values = dict(details or {})
    if key == "stock":
        values.setdefault("already_in_cart", "0")
        values["remaining"] = str(max(Decimal(0), Decimal(values["available_quantity"]) - Decimal(values["already_in_cart"])))
    return MESSAGES[key][language].format(**values)
