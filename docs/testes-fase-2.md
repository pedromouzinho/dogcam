# Testes de falha da fase 2 (2026-10-07)

Testes ao vivo do cérebro DogCam com o Pedro. Horas em hora de Lisboa.
Ordem: 1 → 4 → 5 → 6 → 3 → 2 (o reinício fica para o fim porque fecha a sessão do Claude Code).

| # | Teste | Esperado | Resultado |
|---|---|---|---|
| 1 | Parar o Go Live do iPad 1 min | "câmara sem imagem" e depois "voltou" | **passou** |
| 4 | Falha da API do Claude | "suspeita NÃO confirmada" | **passou** |
| 5 | Anda demasiado (cão simulado) | "anda demasiado" | **passou** (com instância de teste) |
| 6 | /pausa 5 | nenhum alerta do cão durante 5 min, depois volta sozinho | **passou** |
| 3 | Tirar o cabo de rede 2 min | Wi-Fi assume; a câmara fica sem imagem (o iPad não chega ao dogcam pelo Wi-Fi) | por fazer |
| 2 | Reiniciar o servidor | tudo volta sozinho; healthchecks avisa se demorar mais de 3 min | por fazer |

## Detalhes

### 1. Parar o Go Live do iPad (passou)
- 19:04:35 "Câmara sem imagem há 30 s." (enviado; o Frigate leva ~30 s a pôr o camera_fps a 0, por isso chega ~1 min depois de parar)
- 19:04:40 sem ping ao healthchecks.io enquanto não havia imagem (como pedido)
- 19:05:05 "A câmara voltou a ter imagem (esteve sem imagem 1 min 1 s)." O iPad religou-se sozinho ao Go Live.
- O Pedro marcou ambos com ✅.

### Problemas encontrados durante o teste 1
- **Alertas de "CPU presa" (18:55 e 19:03, 900 MHz)**: pareceram falsos (a CPU desce sozinha para 800-900 MHz com a cena calma) e o
  scripts/energia.sh passou a medir outra vez com uma CPU ocupada 3 s. **Correção:** às 19:17 a medição com carga deu 400 MHz (PROCHOT
  verdadeiro, ver abaixo), por isso estes dois podem já ter sido o travão a começar; não dá para saber. O Pedro marcou um ✅ e o outro ❌.
- **Suspeita real às 18:58 marcada ❌ pelo Pedro**: o Claude disse "sem cone 0,80" porque o recorte deixou a cabeça de fora (só se via a ponta do cone). Foi feita com o pedido antigo; o atual manda usar um valor intermédio quando a cabeça não se vê. Por fazer: aumentar a margem dos recortes para a cabeça e o cone caberem.
- Um erro 400 do Telegram ao atualizar os botões (editMessageReplyMarkup) às 19:04:47; a resposta ficou registada na mesma.

### 4. Falha da API do Claude (passou)
- cerebro/tests/falha_claude.py: regra da SUSPEITA com o SDK verdadeiro apontado para um endereço inexistente.
- 19:06:34 a chamada falhou (APIConnectionError, ficou no diário); 20 s depois do início da suspeita saiu
  "TESTE (falha simulada da API): Suspeita NÃO confirmada: mexe-se na cama há 20 s e não consegui confirmar com o Claude (a chamada falhou (APIConnectionError))", entregue com 3 recortes e botões.

### 5. Anda demasiado (passou, com uma instância de teste)
- Uma pessoa a andar não serve: o Frigate só deteta `dog`.
- 1.ª tentativa (19:06:47-19:08:59): cão falso publicado no MQTT do cérebro verdadeiro a andar 130 s no quarto -> **sem alerta**,
  porque o cão verdadeiro estava a ser detetado, deitado e parado na cama, e cada atualização dele ("parado, na cama") reiniciava os 2 min.
  O cérebro assume um só cão: com o cão verdadeiro isso está certo, mas não dá para testar esta regra com eventos falsos enquanto ele é detetado.
  Nota: se o Frigate seguir o mesmo cão como dois objetos (um parado), a contagem também reinicia.
- 2.ª tentativa (19:10:23): cerebro/tests/simular_anda.py, regras novas alimentadas com mensagens MQTT falsas (relógio acelerado), envio verdadeiro:
  "TESTE (cão simulado): Anda demasiado: em movimento fora da cama há 2 min (agora em quarto)." entregue.
  Que o cérebro verdadeiro recebe o MQTT do Frigate já está provado pelos eventos reais no diário.

### 6. /pausa 5 (passou)
- ~19:11 /pausa 5; 19:11:11 /teste -> não enviado, ficou no diário como "alerta_em_pausa".
- A pausa acabou sozinha; 19:17:45 /teste -> "TESTE: alerta de teste do cão (/teste)." enviado com botões.
- Novo comando /teste (alerta do tipo cão, respeita a pausa), com teste automático.

### Problemas encontrados durante o teste 6
- **"Desapareceu" falso às 19:17:25** ("último sítio: fora das zonas"), com o cão deitado no edredão. Causa: o Frigate manda as primeiras
  atualizações de cada deteção sem zonas; o cérebro punha "fora das zonas" e o sítio alternava cama <-> fora das zonas a cada poucos segundos.
  Corrigido: uma lista de zonas vazia não apaga o último sítio conhecido (teste automático novo; 61 testes).
- **PROCHOT verdadeiro às 19:17:56**: "CPU presa a 400 MHz", com o carregador ligado e a carregar (60%). Confirmado: 400 MHz com carga em
  3 medições, Frigate a saltar 1,6 de 3 imagens/s. O alerta funcionou.
- O enquadramento do iPad mudou outra vez (a imagem do alerta é diferente da usada para as zonas).
