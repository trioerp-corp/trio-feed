"""Trio: the shop's activity («نشاطك إيه؟») — the owner types it, the program sets itself up for it.

A profile is plain configuration built only from what the program already knows how to do:
  base        one of modules.BUSINESS (market / retail / cafe / gaming / clinic) — decides the hall, recipes, warehouses…
  categories  item categories made at setup
  units       units offered when adding an item
  fields      extra item fields (part number, brand, size…) — shown on the item, searchable in the cashier when s=True
  item, items the word for «صنف» / «الأصناف» in this activity
  suggest     parts that help this activity (shown as advice / in «⭐ اشترك», never opened for free)
The library below covers the common Egyptian shops; anything else is asked from Trio's Worker (Claude) and checked by
clean() before it is used, so a reply can never add something the program does not have.
"""
import re

from django.core.exceptions import ValidationError

from . import modules as M

F = lambda k, n, s=True: dict(k=k, n=n, s=s)  # noqa: E731
SIZE, COLOR, BRAND = F("size", "المقاس"), F("color", "اللون"), F("brand", "الماركة")
MODEL, SERIAL, WARRANTY, SHELF = F("model", "الموديل"), F("serial", "السيريال / IMEI"), F("warranty", "الضمان", False), F("shelf", "مكان الرف", False)
ORIGIN = F("origin", "بلد المنشأ", False)

LIBRARY = [
    # ---------------------------------------------------------------- food
    dict(key="supermarket", name="سوبر ماركت", base="market", words=["سوبر ماركت", "سوبرماركت", "هايبر", "ماركت"],
         categories=["بقالة وتموين", "ألبان وأجبان", "مشروبات", "حلويات وسناكس", "خضار وفاكهة", "لحوم ودواجن ومجمدات", "منظفات", "عناية شخصية", "أدوات منزلية", "أخرى"],
         units=["قطعة", "كيلو", "علبة", "كرتونة", "لتر", "شكارة"], fields=[BRAND], suggest=["units", "labels", "promos", "partners"]),
    dict(key="grocery", name="بقالة", base="market", words=["بقالة", "بقال", "ميني ماركت", "مينى ماركت", "كشك", "تموين"],
         categories=["بقالة وتموين", "ألبان وأجبان", "مشروبات", "حلويات وسناكس", "منظفات", "سجاير", "كروت شحن", "أخرى"],
         units=["قطعة", "كيلو", "علبة", "كرتونة", "لتر", "باكو"], fields=[], suggest=["units", "partners"]),
    dict(key="vegetables", name="خضار وفاكهة", base="market", words=["خضار", "فاكهة", "فكهاني", "خضري", "خضروات"],
         categories=["خضار", "فاكهة", "ورقيات", "أعشاب", "أخرى"], units=["كيلو", "قفص", "حزمة", "قطعة"], fields=[], suggest=["labels"]),
    dict(key="butcher", name="جزارة", base="market", words=["جزارة", "جزار", "لحوم", "لحمة"],
         categories=["لحم بقري", "لحم ضاني", "مفروم", "كبدة وممبار", "مصنعات لحوم", "أخرى"], units=["كيلو", "قطعة"], fields=[], suggest=["labels", "production"]),
    dict(key="poultry", name="فراخ وطيور", base="market", words=["فراخ", "دواجن", "طيور", "فرارجي", "بانيه"],
         categories=["فراخ بلدي", "فراخ بيضا", "أجزاء", "مصنعات", "بيض", "طيور", "أخرى"], units=["كيلو", "قطعة", "طبق"], fields=[], suggest=["labels", "production"]),
    dict(key="fish", name="أسماك", base="market", words=["سمك", "أسماك", "اسماك", "سماك", "جمبري"],
         categories=["سمك طازة", "مجمدات", "جمبري وكابوريا", "مشويات", "أخرى"], units=["كيلو", "قطعة"], fields=[ORIGIN], suggest=["labels"]),
    dict(key="spices", name="عطارة", base="market", words=["عطارة", "عطار", "توابل", "أعشاب"],
         categories=["توابل", "أعشاب", "حبوب وبقوليات", "زيوت طبيعية", "بخور", "أخرى"], units=["كيلو", "جرام", "علبة", "قطعة"], fields=[ORIGIN], suggest=["labels", "units"]),
    dict(key="roastery", name="محمصة ومكسرات", base="market", words=["محمصة", "مكسرات", "تسالي", "بن", "قهوة سادة"],
         categories=["مكسرات", "لب وسوداني", "بن وقهوة", "شيكولاتة وحلويات", "بذور", "أخرى"], units=["كيلو", "جرام", "علبة"], fields=[ORIGIN], suggest=["labels", "production"]),
    dict(key="bakery", name="حلواني ومخبز", base="cafe", words=["حلواني", "حلويات", "مخبز", "فرن", "عيش", "جاتوه", "تورت", "كيك"],
         categories=["جاتوه وتورت", "حلويات شرقي", "مخبوزات", "بسكويت وكعك", "خامات", "أخرى"], units=["قطعة", "كيلو", "علبة", "صينية"], fields=[], suggest=["recipes", "production"]),
    dict(key="dairy", name="ألبان", base="market", words=["ألبان", "البان", "لبن", "جبن", "جبنة", "زبادي"],
         categories=["لبن", "جبن", "زبادي ورايب", "زبدة وقشطة", "بيض", "أخرى"], units=["كيلو", "قطعة", "لتر", "علبة"], fields=[], suggest=["labels", "expiry"]),
    # ---------------------------------------------------------------- health & beauty
    dict(key="pharmacy", name="صيدلية", base="retail", words=["صيدلية", "صيدليه", "أجزخانة", "اجزخانة", "فارمسي", "ادوية", "أدوية"],
         categories=["أدوية", "مستلزمات طبية", "عناية بالبشرة", "أطفال ورضع", "مكملات غذائية", "تجميل", "أخرى"],
         item="صنف", items="الأصناف", units=["علبة", "شريط", "قطعة", "زجاجة", "أمبول"],
         fields=[F("generic", "المادة الفعالة"), F("company", "الشركة"), SHELF], suggest=["units", "expiry", "returns", "partners"]),
    dict(key="medical", name="مستلزمات طبية", base="retail", words=["مستلزمات طبية", "مستلزمات طبيه", "أجهزة طبية", "اجهزة طبية"],
         categories=["أجهزة قياس", "مستهلكات", "أدوات جراحية", "كراسي وعكاكيز", "أخرى"], units=["قطعة", "علبة", "كرتونة"],
         fields=[BRAND, MODEL, WARRANTY], suggest=["units", "returns", "partners"]),
    dict(key="cosmetics", name="مستحضرات تجميل وبرفانات", base="retail", words=["تجميل", "كوزمتكس", "ميكب", "ميك اب", "برفان", "برفانات", "عطور", "بيوتي"],
         categories=["ميكب", "عناية بالبشرة", "عناية بالشعر", "برفانات", "إكسسوارات", "أخرى"], units=["قطعة", "علبة", "زجاجة"],
         fields=[BRAND, COLOR], suggest=["promos", "labels", "expiry"]),
    dict(key="salon", name="صالون وكوافير", base="retail", words=["صالون", "كوافير", "حلاق", "باربر", "سبا", "بيوتي سنتر"],
         categories=["خدمات شعر", "خدمات بشرة", "حلاقة", "منتجات للبيع", "أخرى"], item="خدمة / منتج", items="الخدمات والمنتجات",
         units=["خدمة", "قطعة"], fields=[], suggest=["partners"]),
    # ---------------------------------------------------------------- auto
    dict(key="auto_parts", name="قطع غيار عربيات", base="retail", words=["قطع غيار", "قطع الغيار", "اسبير", "سبير", "سبيرز", "عربيات", "سيارات", "اوتو", "أوتو"],
         categories=["فلاتر", "فرامل وتيل", "عفشة وتعليق", "كهربا وبطاريات", "زيوت وشحوم", "بوجيهات وإشعال", "تكييف وتبريد", "إكسسوارات", "أخرى"],
         item="قطعة", items="القطع", units=["قطعة", "طقم", "لتر", "علبة"],
         fields=[F("part_no", "رقم القطعة (OEM)"), BRAND, F("cars", "بتركب على (الموديلات)"), F("year", "سنة الموديل"), SHELF],
         suggest=["units", "returns", "partners", "labels"]),
    dict(key="moto_parts", name="قطع غيار موتوسيكلات", base="retail", words=["موتوسيكل", "موتوسيكلات", "سكوتر", "تكتك", "توكتوك", "تروسيكل"],
         categories=["كاوتش وجنوط", "فرامل", "كهربا", "موتور", "زيوت", "إكسسوارات", "أخرى"], item="قطعة", items="القطع", units=["قطعة", "طقم", "لتر"],
         fields=[F("part_no", "رقم القطعة"), F("cars", "بتركب على"), SHELF], suggest=["returns", "partners"]),
    dict(key="tires", name="كاوتش وبطاريات", base="retail", words=["كاوتش", "كاوتشات", "اطارات", "إطارات", "بطاريات", "بطارية", "جنوط"],
         categories=["كاوتش ملاكي", "كاوتش نقل", "بطاريات", "جنوط", "خدمات (ترصيص / ميزان)", "أخرى"], units=["قطعة", "خدمة"],
         fields=[SIZE, BRAND, F("year", "سنة الإنتاج"), WARRANTY], suggest=["partners", "returns"]),
    dict(key="oils", name="زيوت وتشحيم", base="retail", words=["زيوت", "تشحيم", "غيار زيت", "مغسلة", "محطة"],
         categories=["زيوت موتور", "زيوت فتيس", "فلاتر", "مياه رادياتير", "خدمات", "أخرى"], units=["لتر", "جركن", "قطعة", "خدمة"],
         fields=[BRAND, F("grade", "اللزوجة / النوع")], suggest=["units", "partners"]),
    # ---------------------------------------------------------------- building & tools
    dict(key="electric", name="أدوات كهربا", base="retail", words=["كهربا", "كهرباء", "أدوات كهربائية", "ادوات كهربائية", "كهربائي", "إضاءة", "اضاءة", "لمبات"],
         categories=["أسلاك وكابلات", "مفاتيح وبرايز", "لمبات وإضاءة", "لوح وقواطع", "مواسير وعلب", "عدد", "أخرى"], units=["قطعة", "متر", "لفة", "علبة"],
         fields=[BRAND, F("spec", "المواصفة (أمبير / وات / مقاس)")], suggest=["units", "partners", "returns"]),
    dict(key="plumbing", name="سباكة وأدوات صحية", base="retail", words=["سباكة", "سباك", "أدوات صحية", "ادوات صحية", "خلاطات", "مواسير"],
         categories=["مواسير ووصلات", "خلاطات وحنفيات", "أطقم حمامات", "سخانات", "محابس", "عدد", "أخرى"], units=["قطعة", "متر", "طقم"],
         fields=[BRAND, SIZE], suggest=["units", "partners", "returns"]),
    dict(key="hardware", name="حدايد وبويات", base="retail", words=["حدايد", "بويات", "دهانات", "خردوات", "عدد وأدوات", "مسامير"],
         categories=["بويات ودهانات", "مسامير ومسامير قلاووظ", "عدد يدوية", "عدد كهربائية", "لحام", "أخرى"], units=["قطعة", "كيلو", "علبة", "جالون", "بستلة"],
         fields=[BRAND, COLOR, SIZE], suggest=["units", "partners"]),
    dict(key="building", name="مواد بناء", base="retail", words=["مواد بناء", "اسمنت", "أسمنت", "حديد تسليح", "رمل", "طوب"],
         categories=["أسمنت", "حديد", "طوب", "رمل وزلط", "عوازل", "أخرى"], units=["طن", "شكارة", "قطعة", "متر", "نقلة"],
         fields=[BRAND], suggest=["partners", "units", "stores"]),
    dict(key="ceramic", name="سيراميك وأدوات صحية", base="retail", words=["سيراميك", "بورسلين", "رخام", "جرانيت"],
         categories=["سيراميك أرضيات", "سيراميك حوائط", "بورسلين", "رخام وجرانيت", "لاصق وترويب", "أخرى"], units=["متر", "كرتونة", "قطعة"],
         fields=[BRAND, SIZE, COLOR, F("grade", "الفرز")], suggest=["units", "partners", "stores"]),
    # ---------------------------------------------------------------- electronics
    dict(key="mobiles", name="موبايلات وإكسسوارات", base="retail", words=["موبايل", "موبايلات", "محمول", "تليفونات", "اكسسوارات موبايل", "صيانة موبايل"],
         categories=["موبايلات", "إكسسوارات", "شواحن وكابلات", "سماعات", "كروت شحن", "صيانة", "أخرى"], units=["قطعة", "خدمة"],
         fields=[BRAND, MODEL, SERIAL, COLOR, WARRANTY], suggest=["returns", "partners", "labels"]),
    dict(key="computers", name="كمبيوتر ولابتوب", base="retail", words=["كمبيوتر", "لابتوب", "لاب توب", "حاسبات", "طابعات", "أحبار"],
         categories=["لابتوب", "كمبيوتر وشاشات", "قطع غيار", "طابعات وأحبار", "إكسسوارات", "صيانة", "أخرى"], units=["قطعة", "خدمة"],
         fields=[BRAND, MODEL, SERIAL, F("spec", "المواصفات"), WARRANTY], suggest=["returns", "partners"]),
    dict(key="appliances", name="أجهزة كهربائية", base="retail", words=["أجهزة كهربائية", "اجهزة كهربائية", "أجهزة منزلية", "ثلاجات", "غسالات", "تكييفات"],
         categories=["ثلاجات وديب فريزر", "غسالات", "بوتاجازات", "تكييفات", "أجهزة صغيرة", "شاشات", "أخرى"], units=["قطعة"],
         fields=[BRAND, MODEL, SERIAL, COLOR, WARRANTY], suggest=["partners", "returns", "stores"]),
    # ---------------------------------------------------------------- clothes & goods
    dict(key="clothes", name="ملابس", base="retail", words=["ملابس", "هدوم", "بوتيك", "ملابس حريمي", "ملابس رجالي", "ملابس أطفال", "لانجيري"],
         categories=["رجالي", "حريمي", "أطفال", "داخلي", "إكسسوارات", "أخرى"], units=["قطعة", "طقم"],
         fields=[SIZE, COLOR, BRAND, F("season", "الموسم", False)], suggest=["promos", "labels", "returns"]),
    dict(key="shoes", name="أحذية وشنط", base="retail", words=["أحذية", "احذية", "جزم", "شوز", "شنط", "حقائب"],
         categories=["رجالي", "حريمي", "أطفال", "رياضي", "شنط", "أخرى"], units=["جوز", "قطعة"], fields=[SIZE, COLOR, BRAND], suggest=["promos", "labels", "returns"]),
    dict(key="toys", name="لعب أطفال", base="retail", words=["لعب أطفال", "لعب", "ألعاب", "العاب أطفال", "هدايا أطفال"],
         categories=["لعب صغيرة", "عرايس", "عربيات وموتوسيكلات", "تعليمية", "مستلزمات أطفال", "أخرى"], units=["قطعة"], fields=[F("age", "السن المناسب"), BRAND], suggest=["promos", "labels"]),
    dict(key="stationery", name="مكتبة وأدوات مكتبية", base="retail", words=["مكتبة", "ادوات مكتبية", "أدوات مكتبية", "كشاكيل", "تصوير", "طباعة"],
         categories=["كشاكيل وكراسات", "أقلام", "أدوات هندسية", "شنط مدرسية", "تصوير وطباعة", "كتب", "أخرى"], units=["قطعة", "دستة", "علبة", "رزمة", "خدمة"],
         fields=[BRAND], suggest=["units", "promos"]),
    dict(key="gifts", name="هدايا وإكسسوارات", base="retail", words=["هدايا", "اكسسوارات", "إكسسوارات", "انتيكات", "تحف", "فضة"],
         categories=["هدايا", "إكسسوارات", "تحف وأنتيكات", "تغليف", "أخرى"], units=["قطعة"], fields=[COLOR], suggest=["promos", "labels"]),
    dict(key="housewares", name="أدوات منزلية", base="retail", words=["أدوات منزلية", "ادوات منزلية", "أدوات مطبخ", "بلاستيك", "صيني", "زجاج"],
         categories=["مطبخ", "بلاستيك", "صيني وزجاج", "مفروشات", "منظفات", "أخرى"], units=["قطعة", "طقم", "دستة"], fields=[BRAND, COLOR], suggest=["units", "promos"]),
    dict(key="cleaning", name="منظفات", base="retail", words=["منظفات", "تنظيف", "مطهرات", "صابون"],
         categories=["مسحوق غسيل", "منظفات أرضيات", "مطهرات", "صابون", "أدوات تنظيف", "أخرى"], units=["قطعة", "لتر", "جركن", "كرتونة"], fields=[BRAND], suggest=["units", "partners"]),
    dict(key="furniture", name="موبيليا ومفروشات", base="retail", words=["موبيليا", "أثاث", "اثاث", "مفروشات", "سجاد", "ستائر"],
         categories=["غرف نوم", "صالونات وأنتريهات", "سفرة", "مكاتب", "سجاد وستائر", "أخرى"], units=["قطعة", "طقم", "متر"],
         fields=[COLOR, F("material", "الخامة"), SIZE], suggest=["partners", "stores"]),
    # ---------------------------------------------------------------- agriculture & pets
    dict(key="agri", name="مستلزمات زراعية", base="retail", words=["زراعية", "زراعي", "بذور", "تقاوي", "أسمدة", "اسمدة", "مبيدات", "مشتل"],
         categories=["بذور وتقاوي", "أسمدة", "مبيدات", "أدوات ري", "شتلات", "أخرى"], units=["كيلو", "شكارة", "لتر", "عبوة", "قطعة"],
         fields=[BRAND, F("active", "المادة الفعالة")], suggest=["units", "partners", "expiry"]),
    dict(key="feed", name="أعلاف وبيطري", base="retail", words=["أعلاف", "اعلاف", "علف", "بيطري", "بيطرية", "مواشي", "بيت شوب", "حيوانات أليفة", "pet"],
         categories=["أعلاف", "أدوية بيطرية", "مستلزمات حيوانات", "أكل قطط وكلاب", "أخرى"], units=["شكارة", "كيلو", "علبة", "قطعة"],
         fields=[BRAND], suggest=["units", "partners", "expiry"]),
    # ---------------------------------------------------------------- food service & entertainment
    dict(key="cafe", name="كافيه", base="cafe", words=["كافيه", "كافية", "قهوة", "كوفي", "كوفي شوب", "مقهى"],
         categories=["مشروبات ساخنة", "مشروبات باردة وعصائر", "مشروبات معلبة ومياه", "أكل وسناكس", "شيشة", "خامات المطبخ والبار", "أخرى"],
         units=["كوباية", "قطعة", "كيلو", "لتر"], fields=[], suggest=["recipes"]),
    dict(key="restaurant", name="مطعم", base="cafe", words=["مطعم", "مطاعم", "شاورما", "أكل", "فاست فود", "سندوتشات", "برجر"],
         categories=["وجبات", "سندوتشات", "مشويات", "سلطات ومقبلات", "مشروبات", "خامات المطبخ", "أخرى"], units=["وجبة", "قطعة", "كيلو"], fields=[], suggest=["recipes", "production"]),
    dict(key="grill", name="مطعم مشويات", base="cafe", words=["مشويات", "مشاوي", "كباب", "كفتة", "حاتي", "شواية"],
         categories=["مشويات", "وجبات", "سلطات ومقبلات", "مشروبات", "خامات المطبخ", "أخرى"], units=["وجبة", "كيلو", "قطعة"], fields=[], suggest=["recipes", "production"]),
    dict(key="pizza", name="بيتزا وفطائر", base="cafe", words=["بيتزا", "بيتزيريا", "فطائر", "فطاطري", "فطير", "pizza"],
         categories=["بيتزا", "فطائر", "مقبلات", "مشروبات", "خامات المطبخ", "أخرى"], units=["قطعة", "وجبة"], fields=[], suggest=["recipes", "production"]),
    dict(key="koshary", name="كشري وفول وطعمية", base="cafe", words=["كشري", "كشرى", "فول وطعمية", "فول", "طعمية", "فلافل"],
         categories=["كشري", "فول وطعمية", "حلويات", "مشروبات", "خامات المطبخ", "أخرى"], units=["طبق", "قطعة", "وجبة"], fields=[], suggest=["recipes"]),
    dict(key="juice", name="عصائر", base="cafe", words=["عصير", "عصائر", "كوكتيل", "آيس كريم", "ايس كريم"],
         categories=["عصائر فريش", "كوكتيلات", "آيس كريم", "خامات", "أخرى"], units=["كوباية", "قطعة", "كيلو"], fields=[], suggest=["recipes"]),
    dict(key="gaming", name="بلايستيشن وبلياردو", base="gaming", words=["بلايستيشن", "بلاي ستيشن", "بلياردو", "بلي", "سايبر", "جيمنج", "بينج بونج", "playstation", "ps5", "ps4"],
         categories=["مشروبات ساخنة", "مشروبات باردة وعصائر", "مشروبات معلبة ومياه", "أكل وسناكس", "شيشة", "خامات المطبخ والبار", "أخرى"],
         units=["كوباية", "قطعة"], fields=[], suggest=["recipes"]),
    # ---------------------------------------------------------------- clinics (base clinic: patients, bookings, doctors' shares)
    dict(key="clinic", name="عيادة", base="clinic", words=["عيادة", "عياده", "عيادات", "مركز طبي", "مجمع عيادات", "دكتور", "باطنة", "باطنه", "نسا وتوليد", "نساء وتوليد", "انف واذن"],
         categories=["خدمات طبية", "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"], units=["خدمة", "قطعة", "علبة"], fields=[], item="خدمة", items="الخدمات والأصناف", suggest=["stores", "expiry"]),
    dict(key="dental", name="عيادة أسنان", base="clinic", words=["أسنان", "اسنان", "سنان", "تقويم", "زراعة أسنان", "dental"],
         categories=["خدمات طبية", "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"], units=["خدمة", "قطعة", "علبة"], fields=[], item="خدمة", items="الخدمات والأصناف", suggest=["stores", "expiry"]),
    dict(key="derma", name="عيادة جلدية وتجميل", base="clinic", words=["دكتور جلدية", "جلدية", "جلديه", "ليزر", "تجميل بشرة", "بوتوكس", "فيلر", "derma"],
         categories=["خدمات طبية", "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"], units=["خدمة", "جلسة", "قطعة"], fields=[], item="خدمة", items="الخدمات والأصناف", suggest=["stores", "expiry"]),
    dict(key="pediatrics", name="عيادة أطفال", base="clinic", words=["عيادة أطفال", "اطفال وحديثي الولادة", "طب أطفال", "حديثي الولادة"],
         categories=["خدمات طبية", "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"], units=["خدمة", "قطعة"], fields=[], item="خدمة", items="الخدمات والأصناف", suggest=["expiry"]),
    dict(key="eyes", name="عيادة رمد وعيون", base="clinic", words=["رمد", "عيون", "بصريات", "نظارات طبية"],
         categories=["خدمات طبية", "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"], units=["خدمة", "قطعة"], fields=[], item="خدمة", items="الخدمات والأصناف", suggest=["expiry"]),
    dict(key="physio", name="مركز علاج طبيعي", base="clinic", words=["علاج طبيعي", "علاج طبيعى", "فيزيوثيرابي", "تأهيل", "physio"],
         categories=["خدمات طبية", "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"], units=["جلسة", "خدمة", "قطعة"], fields=[], item="جلسة", items="الجلسات والأصناف", suggest=["expiry"]),
]

# ---------------------------------------------------------------- sample items (the trial and the sales page) + the plan we advise
# key: (plan, [(name, price, {field: value})…]) — prices are rough examples
SAMPLES = {
    "supermarket": ("standard", [("سكر 1 كيلو", 38, {"brand": "الأسرة"}), ("زيت عباد 1 لتر", 95, {"brand": "كريستال"}), ("أرز مصري 1 كيلو", 42, {}),
                                 ("لبن كامل 1 لتر", 45, {"brand": "جهينة"}), ("مياه 1.5 لتر", 9, {}), ("مسحوق غسيل 2.5 كيلو", 210, {"brand": "أريال"})]),
    "grocery": ("basic", [("سكر 1 كيلو", 38, {}), ("شاي 250 جرام", 60, {}), ("مكرونة 400 جرام", 18, {}), ("سجاير", 90, {}), ("كارت شحن 25", 30, {}), ("بيبسي كانز", 15, {})]),
    "vegetables": ("basic", [("طماطم", 20, {}), ("بطاطس", 18, {}), ("خيار", 22, {}), ("موز", 35, {}), ("برتقال", 25, {}), ("بقدونس حزمة", 5, {})]),
    "butcher": ("basic", [("لحم بقري كيلو", 420, {}), ("مفروم كيلو", 380, {}), ("كبدة كيلو", 400, {}), ("لحم ضاني كيلو", 480, {}), ("سجق كيلو", 300, {}), ("ممبار كيلو", 150, {})]),
    "poultry": ("basic", [("فراخ بيضا كيلو", 95, {}), ("صدور فراخ كيلو", 210, {}), ("أوراك كيلو", 140, {}), ("بانيه كيلو", 240, {}), ("بيض طبق", 165, {}), ("كبدة وقوانص", 120, {})]),
    "fish": ("basic", [("بلطي كيلو", 85, {"origin": "مصر"}), ("بوري كيلو", 160, {}), ("جمبري وسط كيلو", 450, {}), ("فيليه كيلو", 220, {}), ("سبيط كيلو", 380, {}), ("كابوريا كيلو", 250, {})]),
    "spices": ("basic", [("كمون 100 جرام", 30, {}), ("فلفل أسود 100 جرام", 45, {}), ("كركم 100 جرام", 25, {}), ("ينسون 100 جرام", 30, {}), ("عدس أصفر كيلو", 70, {}), ("بخور عود", 60, {})]),
    "roastery": ("basic", [("لب سوري كيلو", 180, {}), ("سوداني كيلو", 120, {}), ("كاجو كيلو", 900, {"origin": "الهند"}), ("بن محوج 250 جرام", 140, {}), ("لوز كيلو", 700, {}), ("شيكولاتة كيلو", 450, {})]),
    "bakery": ("standard", [("تورتة شيكولاتة", 250, {}, {"sizes": [("صغيرة", 250), ("كبيرة", 450)]}), ("جاتوه قطعة", 35, {}), ("بسبوسة كيلو", 160, {}), ("كرواسون", 25, {}), ("كحك كيلو", 260, {}), ("عيش فينو 5 قطع", 15, {})]),
    "dairy": ("basic", [("لبن جاموسي لتر", 50, {}), ("جبنة بيضا كيلو", 140, {}), ("جبنة رومي كيلو", 380, {}), ("زبادي", 10, {}), ("زبدة فلاحي كيلو", 380, {}), ("قشطة", 40, {})]),
    "pharmacy": ("standard", [("بانادول اكسترا", 45, {"generic": "باراسيتامول", "company": "GSK"}), ("أوجمنتين 1 جم", 120, {"generic": "أموكسيسيلين", "company": "GSK"}),
                              ("كونجستال", 30, {"generic": "باراسيتامول + كلورفينيرامين"}), ("فيتامين سي فوار", 65, {}), ("شاش طبي", 15, {}), ("بامبرز مقاس 4", 320, {})]),
    "medical": ("standard", [("جهاز ضغط ديجيتال", 1200, {"brand": "أومرون", "model": "M2"}), ("جهاز سكر", 650, {"brand": "أكيو تشك"}), ("شرايط سكر 50", 450, {}),
                             ("سرنجات 3 سم علبة", 120, {}), ("كرسي متحرك", 4500, {}), ("ترمومتر ديجيتال", 90, {})]),
    "cosmetics": ("standard", [("برفان رجالي 100 مل", 950, {"brand": "لطافة"}), ("كريم مرطب", 180, {"brand": "نيفيا"}), ("روج", 220, {"color": "أحمر"}),
                               ("شامبو 400 مل", 140, {}), ("ماسكارا", 260, {}), ("صابونة طبية", 55, {})]),
    "salon": ("basic", [("قص شعر", 100, {}), ("حلاقة دقن", 50, {}), ("صبغة", 350, {}), ("بروتين", 900, {}), ("تنظيف بشرة", 300, {}), ("شامبو للبيع", 140, {})]),
    "auto_parts": ("standard", [("تيل فرامل أمامي", 650, {"part_no": "04465-0K240", "brand": "تويوتا", "cars": "كورولا 2014-2018"}),
                                ("فلتر زيت", 120, {"part_no": "90915-YZZE1", "cars": "تويوتا / كيا"}), ("بوجيه إيريديوم", 280, {"brand": "NGK", "cars": "هيونداي النترا"}),
                                ("مساعدين أمامي", 1800, {"brand": "KYB", "cars": "لانسر 2008-2016"}), ("زيت موتور 4 لتر", 1100, {"brand": "موبيل"}), ("سير كاتينة", 750, {"cars": "شيفروليه أفيو"})]),
    "moto_parts": ("basic", [("كاوتش أمامي", 450, {"cars": "دايون"}), ("تيل فرامل", 90, {}), ("بوجيه", 60, {}), ("زيت 1 لتر", 160, {}), ("سلسلة وترس", 550, {}), ("فانوس", 120, {})]),
    "tires": ("standard", [("كاوتش 185/65R15", 3200, {"size": "185/65R15", "brand": "بريدجستون", "year": "2026"}), ("كاوتش 205/55R16", 4100, {"size": "205/55R16", "brand": "ميشلان"}),
                           ("بطارية 70 أمبير", 4200, {"brand": "شامبيون", "warranty": "سنة"}), ("ترصيص 4 عجل", 200, {}), ("ميزان", 150, {}), ("جنط 15", 2500, {})]),
    "oils": ("basic", [("زيت 5W30 4 لتر", 1100, {"brand": "موبيل", "grade": "5W30"}), ("زيت 10W40 4 لتر", 800, {"grade": "10W40"}), ("فلتر زيت", 120, {}),
                       ("مياه رادياتير", 90, {}), ("غيار زيت (خدمة)", 100, {}), ("زيت فتيس", 450, {})]),
    "electric": ("standard", [("سلك 2 مم لفة", 1400, {"brand": "السويدي", "spec": "2 مم"}), ("مفتاح إضاءة", 45, {"brand": "شنايدر"}), ("لمبة ليد 12 وات", 55, {"spec": "12 وات"}),
                              ("قاطع 32 أمبير", 180, {"spec": "32 أمبير"}), ("بريزة", 50, {}), ("لوحة 12 خط", 650, {})]),
    "plumbing": ("standard", [("خلاط حوض", 900, {"brand": "جروهي"}), ("ماسورة 1/2 بوصة", 85, {"size": "1/2 بوصة"}), ("محبس", 160, {}), ("سخان 50 لتر", 4500, {"brand": "أوليمبيك"}),
                              ("طقم حمام", 7500, {}), ("كوع", 10, {})]),
    "hardware": ("standard", [("بوية بلاستيك جالون", 650, {"brand": "جوتن", "color": "أبيض"}), ("مسامير كيلو", 60, {}), ("شنيور", 1800, {"brand": "بوش"}), ("مفك طقم", 250, {}),
                              ("فرشاة دهان", 40, {}), ("سيليكون", 90, {})]),
    "building": ("standard", [("أسمنت شكارة", 190, {"brand": "السويس"}), ("حديد تسليح طن", 38000, {"brand": "عز"}), ("طوب أحمر ألف", 2200, {}), ("رمل نقلة", 2500, {}),
                              ("زلط نقلة", 3500, {}), ("عزل لفة", 900, {})]),
    "ceramic": ("standard", [("سيراميك 60×60 متر", 220, {"brand": "كليوباترا", "size": "60×60", "grade": "فرز أول"}), ("بورسلين 60×120 متر", 520, {"size": "60×120"}),
                             ("سيراميك حوائط متر", 180, {}), ("لاصق شكارة", 120, {}), ("ترويبة", 45, {}), ("رخام متر", 900, {})]),
    "mobiles": ("standard", [("سامسونج A15", 9500, {"brand": "سامسونج", "model": "A15", "warranty": "سنة"}), ("شاحن سريع 25 وات", 350, {"brand": "سامسونج"}),
                             ("سماعة بلوتوث", 650, {}), ("جراب", 120, {}), ("اسكرينة", 80, {}), ("تغيير شاشة (صيانة)", 1200, {})]),
    "computers": ("standard", [("لابتوب ديل", 32000, {"brand": "ديل", "model": "Inspiron 15", "spec": "i5 / 8GB / 512SSD"}), ("ماوس لاسلكي", 250, {}), ("كيبورد", 300, {}),
                               ("هارد SSD 512", 2200, {}), ("حبر طابعة", 900, {}), ("صيانة سوفت وير", 300, {})]),
    "appliances": ("standard", [("ثلاجة 16 قدم", 28000, {"brand": "توشيبا", "model": "GR-EF", "warranty": "5 سنين"}), ("غسالة 8 كيلو", 21000, {"brand": "زانوسي"}),
                                ("بوتاجاز 5 شعلة", 14000, {}), ("تكييف 1.5 حصان", 32000, {"brand": "شارب"}), ("خلاط", 1800, {}), ("شاشة 43 بوصة", 15000, {})]),
    "clothes": ("standard", [("قميص رجالي", 450, {"size": "L", "color": "أبيض"}), ("بنطلون جينز", 650, {"size": "32", "color": "أزرق"}), ("بلوزة حريمي", 380, {"size": "M"}),
                             ("طقم أطفال", 420, {"size": "4 سنين"}), ("تيشيرت", 250, {"size": "XL"}), ("جاكت", 1200, {"size": "L"})]),
    "shoes": ("standard", [("كوتشي رجالي", 1200, {"size": "43", "color": "أسود"}), ("صندل حريمي", 550, {"size": "38"}), ("جزمة أطفال", 450, {"size": "30"}),
                           ("شنطة يد", 900, {"color": "بيج"}), ("شبشب", 150, {"size": "42"}), ("شنطة ظهر", 650, {})]),
    "toys": ("basic", [("عربية ريموت", 650, {"age": "5+ سنين"}), ("عروسة", 350, {"age": "3+ سنين"}), ("ليجو صغير", 800, {}), ("بازل", 180, {}), ("كورة", 150, {}), ("دبدوب", 280, {})]),
    "stationery": ("basic", [("كشكول 100 ورقة", 35, {}), ("قلم جاف علبة", 120, {}), ("أدوات هندسية", 60, {}), ("شنطة مدرسة", 650, {}), ("تصوير ورقة", 1, {}), ("ورق A4 رزمة", 250, {})]),
    "gifts": ("basic", [("ميدالية", 60, {}), ("برواز", 180, {}), ("مج مطبوع", 120, {}), ("علبة هدايا", 90, {}), ("ساعة حائط", 350, {}), ("ورق تغليف", 15, {})]),
    "housewares": ("standard", [("طقم حلل", 2800, {"brand": "تيفال"}), ("طقم كوبايات", 250, {}), ("سلة بلاستيك", 90, {}), ("ترمس", 220, {}), ("طقم أطباق", 750, {}), ("مفرش سفرة", 300, {})]),
    "cleaning": ("basic", [("مسحوق 2.5 كيلو", 210, {"brand": "بيرسيل"}), ("كلور جركن", 70, {}), ("صابون سايل", 45, {}), ("مطهر 1 لتر", 85, {"brand": "ديتول"}), ("مساحة", 60, {}), ("جوانتي", 25, {})]),
    "furniture": ("standard", [("غرفة نوم كاملة", 65000, {"material": "زان"}), ("أنتريه", 28000, {}), ("سفرة 8 كراسي", 42000, {}), ("مكتب", 6500, {}), ("سجادة 2×3", 3500, {"size": "2×3"}), ("ستارة متر", 250, {})]),
    "agri": ("standard", [("تقاوي طماطم", 450, {"brand": "هازيرا"}), ("سماد يوريا شكارة", 1150, {}), ("مبيد حشري لتر", 650, {"active": "لامبادا سيهالوثرين"}), ("خرطوم ري", 900, {}),
                         ("شتلة ليمون", 120, {}), ("بذور برسيم كيلو", 140, {})]),
    "feed": ("standard", [("علف تسمين شكارة", 950, {}), ("علف دواجن شكارة", 1050, {}), ("أكل قطط 1 كيلو", 220, {"brand": "رويال كانين"}), ("فيتامينات بيطري", 180, {}), ("رمل قطط", 150, {}), ("طوق", 90, {})]),
    "cafe": ("basic", [("قهوة تركي", 35, {}, {"sizes": [("سنجل", 35), ("دوبل", 50)]}), ("شاي", 15, {}), ("نسكافيه", 35, {}), ("عصير مانجو", 45, {}), ("شيشة", 50, {}),
                       ("بيبسي كانز", 20, {}, {"stock": True, "unit": "علبة", "reorder": 12})]),
    "restaurant": ("standard", [("وجبة فراخ مشوية", 120, {}, {"sizes": [("ربع", 120), ("نصف", 220)]}), ("ساندوتش شاورما", 85, {}), ("أرز بالخلطة", 40, {}), ("سلطة خضراء", 25, {}),
                                ("بيبسي كانز", 20, {}, {"stock": True, "unit": "علبة", "reorder": 12}), ("مياه صغيرة", 10, {}, {"stock": True, "unit": "زجاجة", "reorder": 12})]),
    "grill": ("standard", [("كباب", 230, {}, {"sizes": [("ربع كيلو", 230), ("نصف كيلو", 450)]}), ("كفتة", 180, {}, {"sizes": [("ربع كيلو", 180), ("نصف كيلو", 350)]}),
                           ("فراخ مشوية", 120, {}, {"sizes": [("ربع", 120), ("نصف", 220), ("فرخة كاملة", 420)]}), ("سلطة طحينة", 20, {}), ("سلطة خضراء", 20, {}),
                           ("بيبسي كانز", 20, {}, {"stock": True, "unit": "علبة", "reorder": 12})]),
    "pizza": ("standard", [("بيتزا مارجريتا", 120, {}, {"sizes": [("صغير", 120), ("وسط", 160), ("كبير", 210)]}),
                           ("بيتزا سوبر سوبريم", 150, {}, {"sizes": [("صغير", 150), ("وسط", 200), ("كبير", 260)]}), ("فطيرة مشكل جبن", 90, {}), ("فطير مشلتت", 120, {}),
                           ("بطاطس محمرة", 35, {}), ("بيبسي كانز", 20, {}, {"stock": True, "unit": "علبة", "reorder": 12})]),
    "koshary": ("basic", [("كشري", 30, {}, {"sizes": [("صغير", 30), ("وسط", 40), ("كبير", 55)]}), ("ساندوتش فول", 10, {}), ("ساندوتش طعمية", 10, {}),
                          ("طبق طعمية", 20, {}), ("أرز باللبن", 25, {}), ("بيبسي كانز", 20, {}, {"stock": True, "unit": "علبة", "reorder": 12})]),
    "juice": ("basic", [("عصير قصب", 15, {}), ("مانجو", 40, {}), ("فراولة", 35, {}), ("كوكتيل", 50, {}), ("آيس كريم بولة", 20, {}), ("سموزي", 55, {})]),
    "gaming": ("basic", [("شاي", 15, {}), ("قهوة تركي", 30, {}), ("نسكافيه", 35, {}), ("إندومي", 35, {}, {"stock": True, "unit": "كيس", "reorder": 12}), ("بيبسي كانز", 20, {}, {"stock": True, "unit": "علبة", "reorder": 12}), ("مياه صغيرة", 10, {}, {"stock": True, "unit": "زجاجة", "reorder": 12})]),
    # clinics: consumables / medicines kept in stock (services and their prices are set on «الخدمات والأسعار»)
    "clinic": ("basic", [("جوانتي طبي (زوج)", 5, {}), ("سرنجة 5 سم", 4, {}), ("قطن طبي", 25, {}), ("كحول 70%", 40, {}), ("بلاستر", 15, {}), ("خافض حرارة شراب", 35, {})]),
    "dental": ("standard", [("جوانتي طبي (زوج)", 5, {}), ("أمبول بنج", 35, {}), ("حشو كومبوزيت", 450, {}), ("سرنجة أسنان", 6, {}), ("قطن رول", 30, {}), ("فرشة أسنان", 40, {})]),
    "derma": ("standard", [("جوانتي طبي (زوج)", 5, {}), ("كريم واقي شمس", 350, {}), ("سيرم", 420, {}), ("شاش معقم", 15, {}), ("كحول 70%", 40, {}), ("كريم مرطب", 180, {})]),
    "pediatrics": ("basic", [("خافض حرارة شراب", 35, {}), ("سرنجة 3 سم", 4, {}), ("خافض لسان", 2, {}), ("محلول جفاف", 15, {}), ("قطن طبي", 25, {}), ("جوانتي طبي (زوج)", 5, {})]),
    "eyes": ("basic", [("قطرة مرطبة", 60, {}), ("قطرة مضاد حيوي", 75, {}), ("جوانتي طبي (زوج)", 5, {}), ("قطن طبي", 25, {}), ("شاش معقم", 15, {}), ("نظارة طبية", 900, {})]),
    "physio": ("basic", [("جل موجات", 80, {}), ("رباط ضاغط", 60, {}), ("شريط كينزيو", 150, {}), ("حزام ظهر", 450, {}), ("كمادة", 90, {}), ("كريم مسكن", 70, {})]),
}
# the category of each sample (index in the activity's categories)
CATIDX = dict(supermarket=[0, 0, 0, 1, 2, 6], grocery=[0, 0, 0, 5, 6, 2], vegetables=[0, 0, 0, 1, 1, 2], butcher=[0, 2, 3, 1, 4, 3], poultry=[1, 2, 2, 3, 4, 2],
              fish=[0, 0, 2, 1, 2, 2], spices=[0, 0, 0, 1, 2, 4], roastery=[1, 1, 0, 2, 0, 3], bakery=[0, 0, 1, 2, 3, 2], dairy=[0, 1, 1, 2, 3, 3],
              pharmacy=[0, 0, 0, 4, 1, 3], medical=[0, 0, 1, 1, 3, 0], cosmetics=[3, 1, 0, 2, 0, 1], salon=[0, 2, 0, 0, 1, 3], auto_parts=[1, 0, 5, 2, 4, 8],
              moto_parts=[0, 1, 2, 4, 3, 2], tires=[0, 0, 2, 4, 4, 3], oils=[0, 0, 2, 3, 4, 1], electric=[0, 1, 2, 3, 1, 3], plumbing=[1, 0, 4, 3, 2, 0],
              hardware=[0, 1, 3, 2, 0, 0], building=[0, 1, 2, 3, 3, 4], ceramic=[0, 2, 1, 4, 4, 3], mobiles=[0, 2, 3, 1, 1, 5], computers=[0, 4, 4, 2, 3, 5],
              appliances=[0, 1, 2, 3, 4, 5], clothes=[0, 0, 1, 2, 0, 0], shoes=[0, 1, 2, 4, 0, 4], toys=[2, 1, 3, 3, 0, 0], stationery=[0, 1, 2, 3, 4, 4],
              gifts=[1, 2, 0, 3, 2, 3], housewares=[0, 2, 1, 0, 2, 3], cleaning=[0, 1, 3, 2, 4, 4], furniture=[0, 1, 2, 3, 4, 4], agri=[0, 1, 2, 3, 4, 0],
              feed=[0, 0, 3, 1, 2, 2], cafe=[0, 0, 0, 1, 4, 2], restaurant=[0, 1, 0, 3, 4, 4], grill=[0, 0, 0, 2, 2, 3], pizza=[0, 0, 1, 1, 2, 3], koshary=[0, 1, 1, 1, 2, 3], juice=[0, 0, 0, 1, 2, 0], gaming=[0, 0, 0, 3, 2, 2],
              clinic=[1, 1, 1, 1, 1, 2], dental=[1, 1, 1, 1, 1, 3], derma=[1, 2, 2, 1, 1, 2], pediatrics=[2, 1, 1, 2, 1, 1], eyes=[2, 2, 1, 1, 1, 3],
              physio=[1, 3, 1, 3, 3, 2])
for _p in LIBRARY:
    _plan, _items = SAMPLES.get(_p["key"], ("standard", []))
    _ix = CATIDX.get(_p["key"], [])
    _p["plan"], _p["samples"] = _plan, [[r[0], r[1], r[2], _p["categories"][_ix[i]] if i < len(_ix) and _ix[i] < len(_p["categories"]) else ""]
                                        + ([dict(r[3])] if len(r) > 3 else []) for i, r in enumerate(_items)]

BY_KEY = {p["key"]: p for p in LIBRARY}
BASE_DEFAULT = {"market": "supermarket", "retail": "clothes", "cafe": "cafe", "gaming": "gaming", "clinic": "clinic"}


def norm(t):
    t = re.sub(r"[ً-ْـ]", "", str(t or "").lower())  # tashkeel / tatweel
    t = t.translate(str.maketrans("أإآٱةىؤئ", "اااايويي"))
    t = re.sub(r"[^\w\s]", " ", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def match(text):
    """The closest library activity for what the owner typed (None when nothing fits)."""
    n = norm(text)
    if len(n.strip()) < 2:
        return None
    best, score = None, 0
    for p in LIBRARY:
        for w in [p["name"]] + p["words"]:
            nw = norm(w).strip()
            if not nw:
                continue
            if f" {nw} " in n or (len(nw) >= 4 and nw in n):
                sc = len(nw) + (3 if f" {nw} " in n else 0)
                if sc > score:
                    best, score = p, sc
    return best


def _s(x, n=40):
    return re.sub(r"\s+", " ", re.sub(r"[<>\"`{}]", "", str(x or ""))).strip()[:n]


def _opt(o):
    """A sample's options, made safe: stock (a can / bottle kept in stock), unit, reorder, sizes [(name, price)…]."""
    if not isinstance(o, dict):
        return {}
    out = {}
    if o.get("stock"):
        out["stock"] = True
        if _s(o.get("unit"), 15):
            out["unit"] = _s(o.get("unit"), 15)
        try:
            r = float(o.get("reorder"))
            if 0 <= r < 100000:
                out["reorder"] = r
        except (TypeError, ValueError):
            pass
    sizes = []
    for z in (o.get("sizes") or [])[:6] if not out.get("stock") else []:
        try:
            n, pr = _s(z[0], 30), float(z[1])
        except (TypeError, ValueError, IndexError):
            continue
        if n and 0 < pr < 1_000_000 and n not in {a for a, _ in sizes}:
            sizes.append([n, pr])
    if len(sizes) >= 2:
        out["sizes"] = sizes
    return out


def clean(p, text=""):
    """Only what the program knows: a profile from the library or from the AI, made safe."""
    if not isinstance(p, dict):
        return None
    base = p.get("base") if p.get("base") in M.BUSINESS else "retail"
    cats = [c for c in dict.fromkeys(_s(c) for c in (p.get("categories") or []) if _s(c))][:14]
    if "أخرى" not in cats:
        cats.append("أخرى")
    units = [u for u in dict.fromkeys(_s(u, 15) for u in (p.get("units") or []) if _s(u, 15))][:8] or ["قطعة"]
    fields, seen = [], set()
    for f in (p.get("fields") or [])[:6]:
        if not isinstance(f, dict):
            continue
        n = _s(f.get("n"), 30)
        k = re.sub(r"[^a-z0-9_]", "", str(f.get("k") or "").lower())[:16] or f"f{len(fields) + 1}"
        if n and k not in seen:
            seen.add(k)
            fields.append(dict(k=k, n=n, s=bool(f.get("s", True))))
    suggest = [m for m in dict.fromkeys(p.get("suggest") or []) if m in M.MODULES and M.fits(m, base)][:6]
    samples = []
    for x in (p.get("samples") or [])[:8]:
        if isinstance(x, dict):  # from the AI
            x = [x.get("name"), x.get("price"), x.get("extra") or {}, x.get("category") or ""]
        try:
            nm, pr = _s(x[0], 60), float(x[1])
        except (TypeError, ValueError, IndexError):
            continue
        ex = {k: _s(v, 60) for k, v in (x[2] if len(x) > 2 and isinstance(x[2], dict) else {}).items() if k in seen and _s(v, 60)}
        cat = _s(x[3], 40) if len(x) > 3 and isinstance(x[3], str) else ""
        if nm and 0 <= pr < 10_000_000:
            row = [nm, pr, ex, cat if cat in cats else ""]
            opt = _opt(x[4] if len(x) > 4 else None)
            if opt:
                row.append(opt)
            samples.append(row)
    plan = p.get("plan") if p.get("plan") in M.PLANS else "standard"
    return dict(samples=samples, plan=plan, key=_s(p.get("key"), 30) or "custom", name=_s(p.get("name"), 60) or _s(text, 60) or M.BUSINESS[base][0], base=base,
                categories=cats, units=units, fields=fields, item=_s(p.get("item"), 20) or "صنف", items=_s(p.get("items"), 25) or "الأصناف",
                suggest=suggest, text=_s(text or p.get("text"), 120), src=_s(p.get("src"), 10) or "lib")


def for_base(base):
    return clean(dict(BY_KEY[BASE_DEFAULT.get(base, "supermarket")], src="base"))


def resolve(text, ask_ai=None):
    """Library first (offline, free); then Trio's AI; then the nearest base."""
    p = match(text)
    if p:
        return clean(dict(p, src="lib"), text)
    if ask_ai:
        try:
            ai = ask_ai(text)
        except Exception:  # noqa: BLE001
            ai = None
        if ai:
            got = clean(dict(ai, src="ai"), text)
            if got:
                return got
    return clean(dict(key="custom", name=_s(text, 60) or "محل", base="retail", categories=["منتجات", "خدمات"], units=["قطعة", "خدمة"],
                      fields=[BRAND], suggest=["returns", "partners"], src="guess"), text)


def add_categories(names):
    """Categories of a (new) activity that the shop does not have yet — same accounts as «أخرى»."""
    from inventory.models import ItemCategory

    tpl = ItemCategory.objects.filter(name="أخرى").first() or ItemCategory.objects.exclude(name__startswith="مستلزمات تشغيل").first()
    if not tpl:
        return 0
    n = 0
    for c in names:
        if c and not ItemCategory.objects.filter(name=c).exists():
            ItemCategory.objects.create(name=c[:80], stock_account=tpl.stock_account, farm_account=tpl.farm_account, admin_account=tpl.admin_account,
                                        needs_equipment=False)
            n += 1
    return n


def current(s=None):
    """The shop's activity (copies set up before 1.8.0: the default of their business)."""
    from .models import CompanySettings

    s = s or CompanySettings.get()
    return s.activity if isinstance(s.activity, dict) and s.activity.get("categories") else for_base(s.business)


APPAREL = ("clothes", "shoes")  # sizes and colours: no expiry dates, no cartons, no scale


def unused(s=None):
    """The shared parts that do not belong to this shop's activity ({"expiry", "units", "scale"} for clothes / shoes), unless the
    shop already uses them (an expiry typed on a purchase, a carton unit added) — hidden, never removed."""
    from inventory.models import ItemUnit, StockMoveLine

    act = current(s)
    out = set()
    if act.get("key") in APPAREL:
        out = {"expiry", "units", "scale"}
        if StockMoveLine.objects.filter(expiry__isnull=False).exists():
            out.discard("expiry")
        if ItemUnit.objects.exists():
            out.discard("units")
    if act.get("key") and "production" not in (act.get("suggest") or []):  # 1.24.1: «التصنيع» only for shops that make things
        from inventory.models import ProductionOrder

        if not ProductionOrder.objects.exists():
            out.add("production")
    return out


def lookup(request, text):
    """resolve() with the result kept in the session (the preview and the save ask once)."""
    from . import ai

    key = "act:" + norm(text).strip()[:80]
    got = request.session.get(key)
    if isinstance(got, dict) and got.get("categories"):
        return got
    p = resolve(text, ai.profile if ai.enabled() else None)
    request.session[key] = p
    return p


def view(p):
    """For the preview card."""
    return dict(p, base_name=M.BUSINESS[p["base"]][0], suggest_names=[M.MODULES[m][0] for m in p["suggest"]],
                hall=p["base"] == "gaming", recipes=p["base"] in ("cafe", "gaming"), clinic=p["base"] == "clinic",
                tables=p["base"] == "cafe")


from inventory.reorder import DEFAULT as DEFAULT_REORDER  # noqa: E402


CANNED = ("كانز", "علبة", "مياه", "بيبسي", "كوكاكولا", "كولا", "سبرايت", "فانتا", "مياة", "ريدبول", "معلب")


def _menu_kind(p, name, cat, opt):
    """Café / restaurant / hall buffet: dishes and prepared drinks are menu items (made to order, no stock, no reorder
    level); canned drinks and bottled water are stock items. From the library (opt) or guessed from the name / category."""
    if p.get("base") not in ("cafe", "gaming"):
        return "stock"
    if opt.get("stock"):
        return "stock"
    if opt.get("sizes"):
        return "menu"
    n = norm(name + " " + (cat or ""))
    return "stock" if any(norm(w).strip() in n for w in CANNED) else "menu"


def _sample_model(name, price, extra, cat, key):
    """A clothes / shoes sample as ONE model with a few sizes / colours around the sample's own (automatic EAN-13 barcodes)."""
    from inventory import variants as VR

    sz, col = str(extra.get("size") or ""), str(extra.get("color") or "")
    sizes = []
    if sz:
        if sz in VR.SIZE_SETS["ملابس"]:
            sizes = VR.SIZE_SETS["ملابس"]
        elif "سن" in sz:
            sizes = ["2 سنين", "4 سنين", "6 سنين", "8 سنين"]
        elif sz.isdigit():
            n = int(sz)
            step = 1 if key == "shoes" else 2
            sizes = [str(n + step * k) for k in range(-2, 3)]
        else:
            sizes = [sz]
    colors = list(dict.fromkeys([col, "أسود" if col != "أسود" else "أبيض"])) if col else []
    try:
        VR.save(name, cat, price, sizes=sizes, colors=colors, reorder=str(DEFAULT_REORDER),
                extra={k: v for k, v in extra.items() if k not in ("size", "color")})
        return True
    except ValidationError:  # never a broken setup: falls back to a plain item
        return False


def add_samples(p):
    """Example items of the activity (the trial), each in the closest of its categories. A café / restaurant gets its
    dishes as menu products (sizes on ONE product) and its canned drinks as stock items in «علبة»."""
    from inventory import menu as MN
    from inventory.models import Item, ItemCategory

    from inventory.names import clash as name_taken

    cats = list(ItemCategory.objects.exclude(name__startswith="مستلزمات تشغيل"))
    if not cats:
        return 0
    other = next((c for c in cats if c.name == "أخرى"), cats[0])
    made = 0
    for row in p.get("samples") or []:
        name, price, extra = row[0], row[1], row[2] if len(row) > 2 else {}
        opt = row[4] if len(row) > 4 and isinstance(row[4], dict) else {}
        if Item.objects.filter(name=name).exists() or name_taken(name):
            continue
        cat = next((c for c in cats if len(row) > 3 and row[3] and c.name == row[3]), None)
        if not cat:
            words = {w for w in norm(name).split() if len(w) > 2}
            cat = max(cats, key=lambda c: (len(words & {w for w in norm(c.name).split() if len(w) > 2}), c is not other)) if words else other
            if not words & {w for w in norm(cat.name).split() if len(w) > 2}:
                cat = next((c for c in cats if c.name == p["categories"][0]), other) if p.get("categories") else other
        if p.get("key") in APPAREL and (extra.get("size") or extra.get("color")) and _sample_model(name, price, extra, cat, p["key"]):
            made += 1  # 1.24.1: a clothes / shoes sample is a model with its sizes × colours, each with its own barcode
            continue
        kind = _menu_kind(p, name, cat.name, opt)
        if kind == "menu":
            sizes = [(n, str(pr)) for n, pr in opt.get("sizes") or []]
            try:
                it = MN.save_product(name[:120], cat, price=None if sizes else (price or None), sizes=sizes)
            except ValidationError:  # an AI sample without a usable price: skipped, never a broken setup
                continue
            if extra:
                it.extra = dict(it.extra or {}, **extra)
                it.save(update_fields=["extra"])
        else:
            unit = opt.get("unit") or (p.get("units") or ["قطعة"])[0]
            if p.get("base") in ("cafe", "gaming") and not opt.get("unit"):
                unit = "علبة"
            if p.get("base") == "clinic" and not opt.get("unit"):  # a consumable is counted in pieces / boxes, never «خدمة» / «جلسة»
                unit = "علبة" if any(w in name for w in ("قطن", "بلاستر", "شاش", "قطرة", "شراب", "كريم", "جل ", "سيرم", "محلول", "أمبول")) else "قطعة"
            reorder = opt.get("reorder", DEFAULT_REORDER)
            Item.objects.create(code=MN._code(), name=name[:150], category=cat, unit=unit, sale_price=price, extra=extra or {},
                                reorder_level=reorder)
        made += 1
    if p.get("base") == "clinic":  # a clinic also starts with its first services, priced as an example
        made += add_services(p)
    return made


# 1.24.1 the services each kind of clinic really sells (name, example price, minutes) — added on setup and from
# «أضف الخدمات الجاهزة» on the clinic setup page; the owner changes the prices there
SERVICES = {
    "clinic": [("كشف", 300, 15), ("استشارة (إعادة)", 150, 10), ("قياس ضغط وسكر", 50, 5), ("حقنة", 50, 5), ("رسم قلب", 200, 15),
               ("غيار جرح", 100, 15), ("خياطة جرح", 300, 20)],
    "dental": [("كشف", 250, 15), ("استشارة (إعادة)", 100, 10), ("حشو عادي (أملغم)", 500, 30), ("حشو تجميلي (كومبوزيت)", 800, 40),
               ("علاج عصب (جذور)", 1500, 60), ("خلع عادي", 400, 20), ("خلع جراحي (ضرس عقل)", 1200, 45), ("تنظيف وإزالة جير", 600, 30),
               ("تلميع وتبييض", 2500, 60), ("تركيبة (طربوش)", 2500, 30), ("أشعة صغيرة (بانوراما موضعية)", 150, 10), ("تركيب تقويم", 8000, 60),
               ("متابعة تقويم", 500, 20), ("زراعة سن", 12000, 90)],
    "derma": [("كشف", 400, 15), ("استشارة (إعادة)", 200, 10), ("جلسة ليزر إزالة شعر", 800, 30), ("تقشير كيميائي", 1000, 30),
              ("جلسة بلازما (PRP)", 1500, 30), ("حقن بوتوكس", 3500, 20), ("حقن فيلر", 5000, 30), ("جلسة تنظيف بشرة", 600, 45),
              ("كي ثآليل / زوائد جلدية", 500, 20)],
    "pediatrics": [("كشف", 300, 15), ("استشارة (إعادة)", 150, 10), ("كشف حديث ولادة", 350, 20), ("متابعة نمو ووزن", 150, 10),
                   ("جلسة بخار (نيبولايزر)", 100, 15), ("تطعيم (خدمة)", 100, 10)],
    "eyes": [("كشف", 350, 15), ("استشارة (إعادة)", 150, 10), ("قياس نظر بالكمبيوتر", 100, 10), ("قياس ضغط العين", 100, 10),
             ("فحص قاع العين", 250, 15), ("مقاس عدسات لاصقة", 200, 15), ("جلسة ليزر", 2000, 30)],
    "physio": [("كشف وتقييم", 300, 30), ("جلسة علاج طبيعي", 250, 45), ("جلسة تأهيل بعد الإصابة", 300, 45), ("جلسة موجات صادمة", 400, 20),
               ("جلسة تمارين علاجية", 200, 45), ("باقة 10 جلسات", 2200, 45)],
}


def preset_services(p):
    key = p.get("key") if isinstance(p, dict) else str(p or "")
    return SERVICES.get(key) or SERVICES["clinic"]


def add_services(p):
    """Adds the preset services of this clinic kind that are not there yet (by name). Returns how many were added."""
    from pos.clinic import services as CL
    from pos.clinic.models import Service

    have = set(Service.objects.values_list("item__name", flat=True))
    made = 0
    for nm, pr, mins in preset_services(p):
        if nm not in have:
            CL.save_service(nm, pr, minutes=mins)
            made += 1
    return made
