# Swish-QR och betallänkar i svky.se

Läge: spec, inte byggd. Se TASK-1673 och TASK-1672.

Källor: Swish "Guide Swish QR code design specification" v1.7.2, avsnitt 6.1,
och den fungerande implementationen i slöjda.de
(`~/workspace/hemslojden/anmälningssystem/app/services/swish.py` och
`qrkod.py`). Slöjda-koden är körd i drift och verifierad mot Rasmus egen
prototyp. Bygg svky.se-varianten som en avskalad kopia av den, inte från
grunden.

## Designen i PDF:ens avsnitt 5 gäller inte längre

PDF v1.7.2 visar en kod byggd av runda prickar, med gradient från lila till
rött, rundade ögon på 0.1875x och ett avkapat nedre högerhörn. **Den varianten
är utgången.** Koderna från Swish nuvarande generator på
swish.nu/marknadsmaterial/qr-generator är vanliga svartvita QR-koder med bara
den runda Swish-symbolen i mitten.

Bygg alltså inte prickmönstret, gradienten eller det avkapade hörnet. Avsnitt
6.1 i PDF:en, som beskriver innehållet, gäller däremot fortfarande.

## Innehållet i QR-koden

    C<payee>;<amount>;<message>;<lock_mask>

- `C` är obligatoriskt prefix.
- `payee` är Swish-numret, bara siffror. Tio siffror.
- `amount` är kronor med två decimaler och decimalkomma: `100,00`. Inte `100`.
- `message` är URL-kodat.
- `lock_mask` styr vad betalaren FÅR ÄNDRA i appen efter skanning. Decimal
  bitmask: payee = 1, amount = 2, message = 4. Bit satt betyder redigerbar.
  Utelämnad mask tolkas som 0.
- Tomma fält behålls som tom sträng mellan semikolonen: `C1231234567;;;6`.

Specens eget exempel, som ett enhetstest bör återskapa exakt:

    C1237856901;100,00;12229445;0

## Applänken för mobil

    swish://payment?data=<URL-kodad JSON>

```json
{"version":"1.0",
 "payee":   {"value":"1231234567"},
 "amount":  {"value":"100"},
 "message": {"value":"Kollekt"}}
```

- `version` är strängen `"1.0"`.
- `amount.value` är en **sträng**, inte ett tal, och i hela kronor utan
  decimalkomma. Formatet skiljer sig alltså från QR-strängens `100,00`.
- `editable` sätts bara som `true` på de fält som får ändras. Nyckeln
  utelämnas helt för låsta fält. Det finns ingen `"editable": false`.
- Utelämna `amount` och `message` helt när de är tomma.

Formatet är inte dokumenterat av Swish utan härlett från appen. Lägg det i en
egen funktion så att det går att byta ut.

## Ritandet

Samma recept som slöjda.de:

- Bibliotek: `qrcode[pil]==8.2`. Rita inte QR-matrisen själv.
- Felkorrigering `ERROR_CORRECT_H` (~30 %). Krävs eftersom mitten täcks av
  symbolen, och en tryckt kod på en anslagstavla blir smutsig.
- Modulstorlek 10.
- Marginal 4 moduler för tryck, 2 för skärm. Fyra är standardens minimum och
  det som gör en tryckt kod läsbar.
- Swish-symbolen på **exakt 25 % av kodens bredd**.
- **Ingen egen vit platta bakom symbolen.** Swish logotypfil bär redan sin
  runda vita bakgrund med genomskinliga hörn, och Swish riktlinjer säger att
  den inte ska få en till bakgrund ovanpå.
- Symbolfilen finns som `symbol-swish.png` (795x795 RGBA) i slöjda-projektet.

Skalningen av symbolen har en fallgrop som slöjda redan löst: PNG-filer bär
ofta svart i färgkanalerna där de är helt genomskinliga. LANCZOS interpolerar
färg och alfa var för sig, så en rak `resize` blandar in den svärtan i
kantpixlarna och ger en mörk frans runt symbolen. Multiplicera färgen med alfa
före skalningen, och komponera för hand i stället för `paste` med alfamask.
Koden för detta ligger i `qrkod.py` (`_skala` och `_komponera`) - kopiera den.

Vanliga kortlänkar (TASK-1672) ritas med samma funktion utan symbol.

## SVG-vägen har en genomskinlig bakgrund

`SvgPathImage` ritar bara den svarta banan. Ingen vit bakgrund läggs in, så
filen är genomskinlig. På vitt papper syns det inte, men lagd på färgat
underlag eller en mörk sida inverteras koden och blir oläsbar.

**Lägg in en vit rektangel över hela ytan, under banan.** Slöjda har inte gjort
det, så ta inte deras SVG rakt av på den punkten.

Symbolen bäddas in som en `data:`-URI, inte som en länk. Filen ska gå att
skicka till ett tryckeri som en enda fil, och en extern bildreferens blir ett
tomt hål där.

## Hur stor symbolen faktiskt blir

Swish säger 25 % av kodens bredd, men säger inte om den tysta zonen räknas in.
Slöjda räknar på hela bilden inklusive marginal. Med en 33-modulers kod blir
symbolen då 28 % av själva koden vid marginal 2, och 31 % vid marginal 4.

Båda avkodas, men 31 % ligger nära vad felkorrigering H klarar. Räkna hellre
andelen mot koden utan marginal, så blir 25 % verkligen 25 %.

## Mätt 2026-09-07

Verifierat programmatiskt med OpenCV:s QR-läsare:

- Kodningen ger exakt specens exempelsträng `C1237856901;100,00;12229445;0`.
- Fem provkoder som PNG avkodas till exakt den sträng som kodades, med och
  utan symbol i mitten.
- SVG renderad via cairosvg avkodas korrekt vid marginal 2 och 4, med och utan
  symbol, **när den först komponeras mot vit bakgrund**. Utan det steget blir
  den genomskinliga bakgrunden svart och koden inverterad.
- Symbolen bäddas in med `href` (SVG 2), inte `xlink:href`. Renderar rätt i
  både Chromium och cairosvg. Skulle ett tryckeriverktyg tappa den, lägg till
  `xlink:href` som dubblett.
- Ett meddelande med mellanslag och å/ö: `Kollekt Härnösands domkyrka` blir
  `Kollekt%20H%C3%A4rn%C3%B6sands%20domkyrka` och kommer tillbaka oskadat.

Kvar att prova på riktig telefon: att Swish-appen öppnar med rätt förifyllda
värden och att rätt fält går att ändra.

## Vad svky.se måste göra annorlunda än slöjda.de

Slöjda har ett fast användningsfall: en deltagare betalar en avgift, och
låsmasken är alltid 7 för QR-koden. I svky.se väljer beställaren själv vilka
fält som får ändras, så låsmasken räknas fram ur tre kryssrutor och applänkens
`editable`-nycklar måste följa samma val.

## Gåvokoden är det svaga stället i applänken

En gåva utan förifyllt belopp och meddelande ger QR-strängen `C1231234567;;;6`,
alltså belopp och meddelande fria. Applänken kan inte uttrycka samma sak: när
fälten är tomma utelämnas nycklarna `amount` och `message` helt, och då finns
det ingenstans att sätta `editable: true`.

Slöjda bygger aldrig en applänk för en gåva, bara en QR-kod
(`swish_gavokod_text`). Vi vet alltså inte om appen tolkar en utelämnad nyckel
som fri eller som frånvarande. Prova det fallet särskilt på telefon innan
gåvoläget släpps.

## Öppna frågor

- Klickräkningen. En Swish-länk har ingen 302, så dagens klickmodell går inte
  att applicera rakt av. Se TASK-1676.
- Meddelandets längd. Schemat för Swish QR-API anger 70 tecken för
  `swishString`, men Swish-appen visar och sparar bara 50. Den snävare gränsen
  är den som gäller. Slöjda kapar vid 50 i meddelandebygget och vid 70 i
  kodningen.
