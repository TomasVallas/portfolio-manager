# Salkkukartta

Offline-työpöytäsovellus, johon kirjaat Nordnetin talletukset, kaupat, osingot ja tilien arvot sekä lainojen nostot ja maksut. Sovellus pitää kirjaa siitä, paljonko Nordnetissä on opintolainaa ja paljonko omaa rahaa, mitä omistat ja paljonko lainaa on jäljellä.

Ulkoasu on sama kuin [Opintolainalaskurissa](https://github.com/TomasVallas/Opintolainalaskuri), mutta värit ovat neutraalimmat, ja valittavana on vaalea ja tumma teema.

## Ominaisuudet

- **Kirjaukset:**
  - Nordnet: talletus, osto, myynti, osinko, nosto, siirto tilien välillä, kulu ja tilin arvo
  - laina: nosto, pääomitus, maksu (lyhennys, korko ja kulut) ja Kelan hyvitys
- **Rahan lähde:** jokaiseen talletukseen merkitään, paljonko siitä on opintolainaa. Loput lasketaan omaksi rahaksi.
- **Yleiskuva:** nettotalletukset, opintolaina, oma raha, lainasaldo, sijoitettu hankinta-arvo, käteinen, salkun arvo ja nettovarallisuus.
- **Kaupat Nordnetin tapaan:** määrä, kurssi, valuutta, kulut ja euromäärä. Jos euromäärää ei vielä ole (kauppa näkyy vain Kaupat-välilehdellä), se arvioidaan viimeisimmän kaupan valuuttakurssilla ja merkitään ≈-merkillä. Täydennä tarkka summa myöhemmin.
- **Omistukset:** kappaleet, keskihinta, kulut ja hankinta-arvo arvopapereittain. Myynnit lasketaan keskihintamenetelmällä.
- **Tuotto:** kirjaa tilin arvo Nordnetistä (Arvo), niin sovellus näyttää tuoton nettotalletuksiin verrattuna. Arvoon lisätään myöhemmät talletukset ja nostot.
- **Kuvaaja:** oma raha ja opintolaina pinottuna, sijoitettu summa, salkun arvo ja lainasaldo päivittäin.
- **Muokkaus:** klikkaa riviä Tapahtumat-välilehdellä. Poiston voi kumota.
- **Muistiinpanot:** vapaa tekstikenttä suunnitelmille ja muistettaville asioille.
- **Vienti:** kaikki kirjaukset CSV-tiedostoon (Excel, puolipiste ja desimaalipilkku).

## Tallennus

Tiedot tallentuvat automaattisesti jokaisen muutoksen jälkeen tiedostoon `tiedot/salkkukartta.json`. Päivän ensimmäinen tallennus ottaa edellisestä versiosta varmuuskopion kansioon `tiedot/varmuuskopiot` (30 viimeisintä päivää).

`tiedot/` on `.gitignore`-tiedostossa, joten tilinumerot ja summat eivät päädy versionhallintaan.

Muun tietotiedoston voi avata näin:

```bash
python salkkukartta.py --tiedosto polku/toinen.json
```

## Asennus ja käynnistys

Tarvitset Python 3.8:n tai uudemman, jossa on Tkinter. Pillow 8.2+ on vapaaehtoinen, mutta ilman sitä grafiikka on karkeampi.

```bash
pip install pillow
```

Käynnistä sovellus:

```bash
python salkkukartta.py
```

Windowsissa voit myös tuplaklikata `Kaynnista.bat`-tiedostoa.

Sovellus toimii täysin ilman verkkoyhteyttä.

### Linux

Ubuntussa ja Debianissa:

```bash
sudo apt install python3-tk python3-pil python3-pil.imagetk
```

```bash
./kaynnista.sh
```

## Laskennan säännöt

- **Nordnetissä:** talletukset − nostot. Siirrot omien tilien välillä eivät muuta summaa.
- **Opintolainaa / omaa rahaa:** talletusten opintolainaosuudet − nostojen opintolainaosuudet. Loput on omaa rahaa.
- **Ostot:** hankinta-arvo sisältää kulut ja valuutanvaihdon (Nordnetin euromäärä).
- **Arvio:** jos euromäärä puuttuu, käytetään saman valuutan viimeisimmän vahvistetun kaupan kurssia (sisältää vaihtokulun). Jos sellaista ei ole, käytetään asetusten varakurssia.
- **Myynti:** hankinta-arvoa vähennetään keskihinnalla, ja erotus kirjataan toteutuneeksi voitoksi tai tappioksi.
- **Salkun arvo:** tilin viimeisin arvokirjaus + sen jälkeiset talletukset, nostot ja siirrot − kulut. Tilit, joilla ei ole arvokirjausta, lasketaan hankinta-arvolla ja käteisellä.
- **Lainan saldo:** nostot + pääomitetut korot − lyhennykset − hyvitys.
- **Nettovarallisuus:** salkun arvo (tai hankinta-arvo + käteinen) − lainojen saldo.

## Vastuuvapaus

Sovellus on oma kirjanpidon apuväline eikä sijoitusneuvontaa. Tarkista summat ja verotiedot Nordnetistä ja pankista.
