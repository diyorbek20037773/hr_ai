# hr_ai — laptopda ishga tushirish

Dastur oddiy gapni HR qidiruv filtrlariga aylantiradi. Masalan, "Toshkentda yashaydigan, 30 yoshdan katta ayollar"
degan gapdan tayyor filtrlar hosil qiladi. Model to'liq sizning kompyuteringizda ishlaydi, so'rovlar tashqariga
chiqmaydi.

## 1. Kerakli narsalar

| | Talab |
|---|---|
| Dastur | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Linux'da Docker Engine). Versiya: Compose 2.24 yoki yangiroq |
| Disk | Kamida 30 GB bo'sh joy |
| Internet | Faqat birinchi ishga tushirishda: taxminan 12 GB dastur va 8 GB model yuklanadi |
| Video karta | NVIDIA, kamida 6 GB VRAM tavsiya etiladi |

NVIDIA kartasi bo'lsa, oxirgi drayverni o'rnating. Kartani dastur o'zi aniqlaydi va mos versiyani tanlaydi.

**Video kartasiz ham ishlaydi, lekin juda sekin:** bitta so'rov bir necha daqiqa oladi. Bu rejimda Docker'ga kamida
10 GB RAM ajratilishi kerak. Mac kompyuterlarda va Windows'dagi AMD/Intel kartalarda ham dastur shu rejimda ishlaydi,
chunki Docker ularning video kartasidan foydalana olmaydi.

## 2. Ishga tushirish

Avval Docker Desktop'ni oching. Keyin kodni yuklab oling: GitHub sahifasida **Code → Download ZIP** ni bosib, arxivni
oching yoki `git clone` qiling.

- **Windows:** papkadagi `start.bat` faylini ikki marta bosing.
- **Linux yoki Mac:** papkada terminal ochib, `bash start.sh` buyrug'ini bering.

Boshqa hech narsa qilish shart emas. Dastur kompyuterni tekshiradi, kerakli narsalarni yuklaydi, modelni ishga
tushiradi va brauzerda sahifani o'zi ochadi.

Birinchi ishga tushirish internet tezligiga qarab **30 daqiqadan 2 soatgacha** davom etishi mumkin. Terminalni
yopmang va laptopni quvvatga ulab qo'ying. Windows'da dastur kompyuterni uyquga ketkazmaydi, Linux va Mac'da uyqu
rejimini o'zingiz o'chiring. Yuklash uzilsa, shu faylni qayta ishga tushiring, u to'xtagan joyidan davom etadi.
Keyingi safar dastur bir-ikki daqiqada ochiladi va internet kerak bo'lmaydi.

## 3. Foydalanish

| Sahifa | Manzil |
|---|---|
| Asosiy sahifa (qidiruv va prompt tahriri) | http://127.0.0.1:8001/ |
| Monitoring | http://127.0.0.1:3001/ |

Asosiy sahifada gap yozib **Qidirish** ni bosing. Natija maydon tagida chiqadi. Video kartada javob odatda 3–10
soniyada keladi.

**Prompt (Qwen'ga beriladigan)** tugmasi modelga berilgan ko'rsatmani ochadi, uni shu yerda tahrirlash mumkin.
Tahrir dastur qayta ishga tushguncha saqlanadi, keyin standart holatga qaytadi.

## 4. Boshqarish

| Amal | Windows | Linux / Mac |
|---|---|---|
| To'xtatish | `start.bat stop` | `bash start.sh stop` |
| Holatni ko'rish | `start.bat status` | `bash start.sh status` |
| Loglarni ko'rish | `start.bat logs` | `bash start.sh logs` |

Windows buyruqlarini loyiha papkasida ochilgan terminalda bering.

## 5. Muammo chiqsa

| Xabar | Yechim |
|---|---|
| Docker o'rnatilmagan yoki ishlamayapti | Docker Desktop'ni o'rnating va oching, keyin qayta ishga tushiring |
| GPU konteynerga ko'rinmayapti | NVIDIA drayverini yangilang. Windows'da Docker Desktop WSL2 rejimida bo'lishi kerak. Linux'da `nvidia-container-toolkit` kerak. Vaqtincha GPU'siz: `start.bat -Cpu` yoki `bash start.sh --cpu` |
| Port band | Windows: `start.bat -Port 8011 -GrafanaPort 3011`. Linux/Mac: `HR_AI_PORT=8011 HR_AI_GRAFANA_PORT=3011 bash start.sh` |
| Docker'ga RAM kam berilgan | Xabardagi ko'rsatma bo'yicha xotirani kamida 10 GB qiling |
| Model yuklanishi uzildi | Internetni tekshirib, qayta ishga tushiring |

**Qidiruv haqida:** hozircha dastur filtrlarni tayyorlab ko'rsatadi, xodimlar ro'yxatini chiqarmaydi. Real HR tizimiga
ulash uchun HR API manzili va haqiqiy ma'lumotnomalar kerak. Hozirgi ma'lumotnoma ID'lari namunaviy. Texnik
tafsilotlar: [README.md](README.md).
