# Namnbytet svk-short till svky.se

Engångsrunbook. Ta bort filen när bytet är gjort och den gamla paketet är
raderat.

## Varför ordningen spelar roll

CI härleder imagenamnet ur `${{ github.repository }}`
(`.github/workflows/docker.yml`). Döps repot om byter publiceringen väg av sig
själv, men **GHCR döper inte om det befintliga paketet**. Efter bytet finns
`ghcr.io/armandur/svk-short` kvar med all historik, och
`ghcr.io/armandur/svky.se` skapas först när nästa push till main byggt.

Commiten som pekar produktionen mot det nya namnet får därför inte mergas
förrän det nya paketet finns. Görs det ändå svarar registret `manifest
unknown` vid nästa `docker compose pull`, och produktionen står kvar på den
image som redan kör - inget avbrott, men deployen går inte att slutföra.

## Ordning

1. **Döp om repot på GitHub**: Settings, Repository name, `svky.se`.
   GitHub lägger en omdirigering, så befintliga git-remotes fortsätter
   fungera. Rätta dem ändå:

   ```sh
   git remote set-url origin git@github.com:Armandur/svky.se.git
   ```

   Det tar samtidigt bort varningen "This repository moved", som kommer av
   att remoten stavar användarnamnet med litet a.

2. **Pusha något till main** så CI publicerar till det nya paketet.
   `IMAGE_NAME` är `${{ github.repository }}`, så det sker automatiskt vid
   första pushen efter omdöpningen. svky har inga `paths`-filter, så vilken
   push som helst duger.

   Den här commiten duger själv: den pekar visserligen produktionens compose
   mot det nya namnet, men compose läses först vid en deploy, och CI bryr sig
   inte om innehållet. Merga den alltså, vänta på grönt, och deploya först
   därefter.

3. **Kontrollera att paketet finns och går att hämta**, från servern:

   ```sh
   docker pull ghcr.io/armandur/svky.se:latest
   ```

   Faller den med `denied` är paketet privat utan att servern har access.
   Nya paket ärver inte automatiskt repots behörigheter. Rätta under
   paketets Settings, "Manage Actions access", innan du går vidare.

4. **Merga `chore/byt-namn-till-svky-se`** och deploya som vanligt.

5. **Radera det gamla paketet** `svk-short` när produktionen kört på det nya
   ett tag. Vänta - så länge det finns kvar går det att rulla tillbaka till
   en gammal digest.

## Det som INTE påverkas

- **Katalogen `~/svk-short` på servern.** Ett omdöpt repo döper inte om en
  utcheckning. Låt den heta som den gör: backupskriptet och eventuella
  cron-rader pekar på den, och en `mv` här sparar ingenting men kan bryta
  något som inte säger ifrån.
- Domänen `svky.se` och Caddy-konfigurationen.
- Serverns GHCR-token, som är kontobunden och inte repobunden.
- Databasen och `.env`-filerna.

## Det som kan påverkas senare

Slöjda bytte reponamn och deras federerade Tailscale-identitet slutade
fungera med `403`, eftersom trust credentialen är knuten till repots NAMN.
Felet syntes som att Tailscale inte kom upp, inte som ett namnbytesproblem.
svky har ingen sådan identitet i dag, men steg 4 i TASK-1087 inför en - görs
namnbytet efter det, uppdatera villkoret i Tailscale-adminen.
