# DogCam — contexto para o Claude Code

Nanny cam com AI para o cão do Pedro, que está a recuperar de uma fratura na pata da frente direita. Usa cone. Não se pode mexer muito nem lamber o penso. O Pedro quer ver o cão ao vivo no iPhone e receber alertas quando ele faz o que não deve.

Fala com o Pedro em português de Portugal. Explica cada passo em linguagem simples antes de o fazer e pede confirmação antes de ações difíceis de desfazer (apagar dados, abrir portas, mexer na rede).

## Máquina
- MacBook Pro 13" 2017 (MacBookPro14,1): i5-7360U, Intel Iris Plus 640, 8 GB RAM, SSD 256 GB
- Ubuntu Server 26.04 LTS, hostname dogcam, utilizador pedro
- Rede: Wi-Fi wlp2s0 (netplan em /etc/netplan/60-wifi.yaml), IP 192.168.1.249. Mais tarde passa para cabo, com um adaptador USB-C TP-Link UE300C
- Já feito: atualizações, a tampa fechada não suspende (/etc/systemd/logind.conf.d/lid.conf), sleep/suspend/hibernate mascarados
- Temperatura: turbo da CPU desligado (máx. 2,3 GHz, /etc/tmpfiles.d/dogcam-no-turbo.conf) e mbpfan a controlar a ventoinha (/etc/mbpfan.conf, valores padrão). Sem isto a CPU chegava aos 97 °C; com isto fica nos 70-77 °C com o Frigate a trabalhar
- Disco do Frigate: disco virtual ext4 de 150 GB (espaço já reservado) em /var/lib/dogcam/frigate-storage.img, montado em ~/dogcam/storage pelo fstab (loop,nofail). A pasta por baixo tem chattr +i, para nada ser gravado no disco principal se o disco virtual não montar
- Carregador: usar SEMPRE o carregador original da Apple. Em 2026-10-07, com um carregador de 60 W de outra marca, a placa-mãe ativou o BD PROCHOT: CPU presa a 400 MHz a 62 °C, Frigate a saltar metade das imagens e "No frames have been received" no iPhone. Trocar para o carregador original não chegou, porque o travão ficou preso; só saiu com um reset ao SMC (desligar o Mac, carregador ligado, Shift+Control+Option esquerdos + botão de ligar durante 10 s). Não se desligou o PROCHOT por software. O scripts/verificar-arranque.sh avisa se a CPU ficar abaixo de 1 GHz ou se o servidor estiver só com a bateria
- Bateria a 63% da capacidade original (3004 de 4790 mAh, 451 ciclos), sem inchaço. Objetivo: limitar a carga a 60% (applesmc-next, a confirmar neste modelo)
- Fase 1 concluída (2026-10-06): Frigate a funcionar e visto no iPhone por Tailscale. Sudo sem palavra-passe removido; agora o sudo pede palavra-passe. Quando um passo precisar de sudo, pede ao Pedro para o correr com `! sudo ...`
- O ecrã do MacBook corta as últimas linhas da consola. Prefere comandos não interativos (-y, DEBIAN_FRONTEND=noninteractive)

## Arquitetura
- Câmaras (ainda não chegaram): C1 Tapo C210 no quarto (RTSP stream1 2K para ver e gravar, stream2 para detetar, áudio nos dois sentidos via go2rtc tapo://); C2 Tapo C320WS na varanda (RTSP). Até chegarem, usa um vídeo de teste em loop como câmara falsa
- Frigate em Docker Compose: deteção de dog com OpenVINO na GPU Intel (/dev/dri), gravação contínua 2-3 dias e eventos 14 dias, zonas cama, quarto, porta, varanda, login ligado (porta 8971)
- Cérebro DogCam (fase 2): serviço Python baseado no dogcam.py. Lê os streams pelo go2rtc e aplica as regras. Chama o Claude (Haiku) só quando há suspeita, com recortes do stream 2K. Alertas por Telegram. Sinal de vida para o healthchecks.io. Alerta "servidor sem corrente" no Telegram quando /sys/class/power_supply/ADP1/online passar a 0 (e aviso quando a corrente voltar), e alerta se a CPU ficar presa abaixo de 1 GHz (PROCHOT). Regra "na dúvida, avisa"
- Acesso de fora: só por Tailscale (tailscale serve, HTTPS). Nunca abrir portas no router e nunca expor nada à internet
- Segredos num ficheiro .env com chmod 600, fora do git: ANTHROPIC_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HEALTHCHECK_URL, palavras-passe RTSP

## Regras de trabalho
- Tudo em ~/dogcam, versionado em git. Repositório privado dogcam no GitHub
- Versões fixas das imagens Docker, com restart: unless-stopped
- Depois de cada passo, verifica que funciona e mostra o resultado ao Pedro
- Nunca escrevas segredos em ficheiros versionados nem na saída do terminal
