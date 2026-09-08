# Modelando um PCF8574 no Renode

O Renode permite executar firmware com modelos de hardware prontos. Quando um periférico não está disponível, é possível implementar seu comportamento em C# e conectá-lo à plataforma simulada.

Este tutorial cria um modelo do periférico **PCF8574, um expansor de oito entradas e saídas digitais controlado via I2C**. Ele permite ampliar os I/Os de um microcontrolador através do barramento I2C. O microcontrolador envia comandos para atuar nas saídas e lê o estado das entradas.

O objetivo é didático: implementar uma versão básica a partir do datasheet, sem usar o código de uma implementação pronta como referência.

Para testar o caso de uso, o **PCF8574 é conectado ao I2C de um STM32F407**. O firmware alterna quatro LEDs pelas saídas P0..P3 e imprime na UART as mudanças dos botões ligados a P4..P7. Ao final, um painel web permite observar os LEDs e acionar os botões, sem placa física.


```text
STM32F407 --I2C1--> PCF8574 --P0..P3--> LEDs
                      ^
                      |
                   P4..P7
                   Botões

STM32 USART2 -----------------------> Terminal UART
```

## 1. Preparar o projeto

Requisitos: [Renode 1.16.1](https://renode.readthedocs.io/en/latest/introduction/installing.html), Python 3.10 ou superior e um editor. O Renode compila o modelo C# durante o carregamento; não é necessário instalar um SDK .NET separado. Os auxiliares usam apenas a biblioteca padrão do Python.

Baixe ou clone este repositório. Abra um terminal na raiz dele e crie uma pasta irmã chamada `meu-pcf8574`. Os comandos transferem apenas o firmware e os auxiliares de interface e testes; o modelo e a plataforma serão criados nas próximas etapas. Use outro nome se a pasta de destino já existir.

**Windows / PowerShell:**

```powershell
$referencia = (Get-Location).Path
New-Item -ItemType Directory ../meu-pcf8574 -ErrorAction Stop
Set-Location ../meu-pcf8574
New-Item -ItemType Directory models, platforms, scripts, firmware, tools, tests, web
Copy-Item "$referencia/firmware/demo.elf" firmware/
Copy-Item "$referencia/tools/renode_client.py" tools/
Copy-Item "$referencia/tools/lab.py" tools/
Copy-Item "$referencia/tests/check.py" tests/
Copy-Item "$referencia/scripts/bridge.py" scripts/
Copy-Item "$referencia/web/index.html" web/
python --version
```

<details>
<summary>Linux / Bash</summary>

```bash
referencia="$PWD"
mkdir ../meu-pcf8574
cd ../meu-pcf8574
mkdir models platforms scripts firmware tools tests web
cp "$referencia/firmware/demo.elf" firmware/
cp "$referencia/tools/renode_client.py" "$referencia/tools/lab.py" tools/
cp "$referencia/tests/check.py" tests/
cp "$referencia/scripts/bridge.py" scripts/
cp "$referencia/web/index.html" web/
python3 --version
```

Use `python3` no lugar de `python` nos próximos comandos, se necessário.

</details>

**Verificação:** `renode --version` deve mostrar a versão instalada, e `python --version`, Python 3.10 ou superior. Os comandos abaixo usam `renode` disponível no PATH.

Todos os caminhos seguintes são relativos à pasta **meu-pcf8574**. Comandos identificados como **Terminal** são executados no PowerShell ou Bash; comandos do **Monitor** são executados dentro do Renode.

## 2. Implementar o PCF8574

O comportamento implementado segue este recorte do [datasheet TI PCF8574, revisão K](https://www.ti.com/lit/ds/symlink/pcf8574.pdf):

| Comportamento | Implementação | Referência |
| --- | --- | --- |
| Pinos inicialmente em nível alto | Latch inicia em `0xFF` | Seção 7.1 |
| Zero escrito força nível baixo | Zero no latch domina o nível observado | Figura 7-2 |
| Um escrito libera o pino com pull-up fraco | Sinal externo pode baixar o nível | Figura 7-2 |
| Leitura observa os pinos | Retornar o estado efetivo, não só o latch | Figura 7-4 |
| Atualização por bytes sucessivos | Processar cada byte recebido | Figura 7-3 |
| A2/A1/A0 em zero | Endereço I2C de sete bits `0x20` | Seção 7.3.3 |

Não há registrador de direção: escrever `1` permite usar o pino como entrada. Neste modelo digital, o nível lógico observado é ditado no código pela operação `outputLatch & externalLevels`. Correntes, resistências, entre outras características elétricas não são simuladas.

**Escritas sucessivas:** a seção 7.3.1 da datasheet afirma que bytes adicionais na mesma transação são ignorados, mas a figura 7-3 mostra dois bytes atualizando o port. O `foreach` abaixo adota o comportamento do diagrama; essa é uma escolha do modelo diante da divergência, não uma conclusão inequívoca do texto. O firmware envia um byte por transação. Transações separadas continuam podendo atualizar o port normalmente.

Crie `models/PCF8574.cs`:

<!-- tutorial-file: models/PCF8574.cs -->
```csharp
using System;
using System.Collections.Generic;
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Tutorial
{
    public class PCF8574 : II2CPeripheral, IGPIOReceiver, INumberedGPIOOutput
    {
        // Last command from the I2C master.
        private byte outputLatch;
        // External levels survive chip reset: a held button stays held.
        private byte externalLevels = 0xFF;

        public IReadOnlyDictionary<int, IGPIO> Connections { get; }

        public PCF8574()
        {
            // Create the eight numbered GPIO connectors.
            var pins = new Dictionary<int, IGPIO>();
            for(var pin = 0; pin < 8; pin++)
            {
                pins.Add(pin, new GPIO());
            }
            Connections = pins;
            Reset();
        }


        // Restore the peripheral's power-on latch state.
        public void Reset()
        {
            outputLatch = 0xFF;
            UpdatePinLevels();
        }

        // II2CPeripheral requires Write, Read and FinishTransmission.
        public void Write(byte[] data)
        {
            // Follow TI Rev. K Figure 7-3: each byte updates the port.
            // Section 7.3.1 conflicts with that diagram; see the README note.
            foreach(var value in data)
            {
                outputLatch = value;
                UpdatePinLevels();
            }
        }

        public byte[] Read(int count = 1)
        {
            var data = new byte[count];
            for(var i = 0; i < count; i++)
            {
                data[i] = EffectivePinLevels;
            }
            return data;
        }

        public void FinishTransmission()
        {
            // No register pointer or partial command to discard on STOP.
        }

        // IGPIOReceiver requires OnGPIO to receive external pin changes.
        public void OnGPIO(int number, bool value)
        {
            if(number < 0 || number >= 8)
            {
                throw new ArgumentOutOfRangeException(nameof(number));
            }
            var mask = (byte)(1 << number);
            if(value)
            {
                externalLevels |= mask;
            }
            else
            {
                externalLevels &= (byte)~mask;
            }
            UpdatePinLevels();
        }

        // Combine the master's command with the externally driven levels.
        // Latch 0 actively pulls the pin low: 0 & external = 0.
        // Latch 1 releases the pin: 1 & external = external.
        // An external low can override a released pin, but an external high
        // cannot override a latched low. AND models this digitally, not electrically.
        private byte EffectivePinLevels => (byte)(outputLatch & externalLevels);

        private void UpdatePinLevels()
        {
            for(var pin = 0; pin < 8; pin++)
            {
                Connections[pin].Set((EffectivePinLevels & (1 << pin)) != 0);
            }
        }

    }
}
```

`II2CPeripheral` atende o barramento I2C; `IGPIOReceiver` recebe mudanças externas dos botões; `INumberedGPIOOutput` expõe os oito conectores de saída, usados pelos LEDs neste exemplo. Esses contratos separam o [comportamento do periférico](https://renode.readthedocs.io/en/latest/advanced/writing-peripherals.html) das conexões da plataforma.

O latch guarda o comando do mestre (Nesse exemplo um STM32), enquanto `externalLevels` guarda o sinal externo. `OnGPIO` altera um bit desse sinal e `UpdatePinLevels` publica o resultado nos conectores. O reset não solta um botão que continua pressionado externamente.

**Verificação de compilação do PCF8574.cs, no Terminal:**

Execute o Renode diretamente, a partir da pasta `meu-pcf8574`.

```sh
renode --console --disable-gui --plain models/PCF8574.cs
```

`--console` abre o Monitor no terminal, `--disable-gui` desativa a interface gráfica e `--plain` simplifica a apresentação. O arquivo passado no final é carregado na inicialização. Também é possível abrir o Renode sem esse argumento e executar no **Monitor**:

```text
include @models/PCF8574.cs
```

O Renode deve carregar `PCF8574.cs` e retornar ao prompt `(monitor)` sem erros de compilação. Digite `quit` para sair. Isso verifica a compilação; a próxima etapa instancia o dispositivo para verificar seu comportamento.

**Atalho opcional do projeto:**

```sh
python tools/lab.py --monitor --script models/PCF8574.cs
```

O auxiliar executa o mesmo comando, acrescentando `--config <arquivo-temporario>` e isolando `TEMP`, `TMP` e `TMPDIR` para não depender da configuração pessoal. Ele não é necessário para compilar ou carregar modelos.

## 3. Arquivos .repl/.resc, Conectando o modelo a LEDs e botões

Um arquivo **`.repl` descreve uma plataforma do Renode**: quais modelos instanciar, seus parâmetros e suas conexões. Um arquivo **`.resc`, são scripts do Renode**, reunindo comandos do Monitor do Renode para montar e executar uma sessão.

Nesta etapa, a máquina terá **somente o PCF8574, quatro LEDs e quatro botões**, sem STM32, controlador I2C ou qualquer firmware para executar.

Crie `platforms/pcf8574.repl`:

<!-- tutorial-file: platforms/pcf8574.repl -->
```text
pcf8574: Tutorial.PCF8574 @ sysbus
    preinit:
        include @models/PCF8574.cs
    0 -> led0@0
    1 -> led1@0
    2 -> led2@0
    3 -> led3@0

led0: Miscellaneous.LED @ sysbus
    invert: true
led1: Miscellaneous.LED @ sysbus
    invert: true
led2: Miscellaneous.LED @ sysbus
    invert: true
led3: Miscellaneous.LED @ sysbus
    invert: true

button4: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@4
button5: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@5
button6: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@6
button7: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@7
```

### O que cada declaração significa

| Trecho | Significado |
| --- | --- |
| `pcf8574:` | Nome desta instância na plataforma |
| `Tutorial.PCF8574` | Classe C# do modelo, relativa a `Antmicro.Renode.Peripherals` |
| `@ sysbus` | Registra o objeto na máquina, sem endereço de memória nesta declaração |
| `preinit: include @models/PCF8574.cs` | Carrega o código antes de criar a instância; `@` aqui identifica um caminho de arquivo |
| `0 -> led0@0` | Liga `Connections[0]` do PCF à entrada zero do LED |
| `-> pcf8574@4` | Liga a saída do botão ao pino P4, recebido por `OnGPIO(4, value)` |

`sysbus` existe em toda máquina Renode; usá-lo aqui **não adiciona uma CPU nem cria uma conexão I2C**. O endereço I2C será definido na próxima etapa. A indentação agrupa os atributos de cada dispositivo; use espaços. Referência: [descrição de plataformas](https://renode.readthedocs.io/en/latest/basic/describing_platforms.html).

Os LEDs representam VCC, resistor, LED e pino do PCF: **nível baixo acende**, por isso `invert: true`. Nos botões, essa opção faz a pressão enviar zero e a liberação enviar um.

Crie `scripts/platform.resc`:

<!-- tutorial-file: scripts/platform.resc -->
```text
mach create "pcf8574-lab"
using sysbus
machine LoadPlatformDescription @platforms/pcf8574.repl
```

`mach create` cria a máquina; `using sysbus` permite abreviar os nomes no Monitor; `LoadPlatformDescription` monta os dispositivos e conexões dos IOs descritas no REPL.

### Verificar as entradas ao pressionar os botões

**Terminal:**

```sh
renode --console --disable-gui --plain scripts/platform.resc
```

Após reset, o latch contém `0xFF`: todos os pinos estão liberados e podem ser usados como entradas. Não é necessário um mestre para que um botão altere o nível observado.

Os nomes usados no Monitor vêm das declarações do REPL: `pcf8574:`, `led0:` e `button4:`. Como esses objetos foram registrados com `@ sysbus`, seus caminhos são `sysbus.pcf8574`, `sysbus.led0` e `sysbus.button4`. Se uma instância for renomeada no REPL, o comando também precisa usar o novo nome.

Execute os passos abaixo no **Monitor** aberto pelo comando anterior, mantendo a mesma sessão.

**1. Consultar o estado inicial do PCF8574**

<!-- tutorial-model-monitor -->
```text
python "dev = monitor.Machine['sysbus.pcf8574']; print(list(dev.Read(1)))"
```

`python` executa código no interpretador embutido do Renode, não no Python do terminal. `monitor.Machine[...]` localiza a instância pelo caminho registrado; `dev` é apenas uma variável para reutilizá-la nos próximos comandos, não um nome definido no REPL.

`Read(1)` chama o método C# do modelo e solicita um byte. `list(...)` e `print(...)` exibem o resultado em decimal. **Esperado: `[255]`, equivalente a `0xFF`**, com todos os pinos liberados.

**2. Conferir o LED0**

<!-- tutorial-model-monitor -->
```text
sysbus.led0 State
```

O primeiro termo identifica o LED declarado como `led0:`; `State` consulta sua propriedade de estado. **Esperado: `False`**, pois o LED é ativo em zero e P0 está alto após reset.

**3. Pressionar o botão ligado a P4**

<!-- tutorial-model-monitor -->
```text
sysbus.button4 Press
```

`Press` aciona o modelo declarado como `button4:`. O nome do botão não determina seu destino: é a conexão `-> pcf8574@4` no REPL que o liga a P4. Com `invert: true`, pressionar envia nível baixo para `OnGPIO(4, false)` do PCF8574.

**4. Aplicar o evento na simulação**

<!-- tutorial-model-monitor -->
```text
emulation RunFor "0.001"
```

Avança **1 ms de tempo virtual** e para novamente. Esse avanço permite entregar o evento do botão mesmo sem CPU; não é uma espera de 1 ms no computador.

**5. Ler o resultado**

<!-- tutorial-model-monitor -->
```text
python "print(list(dev.Read(1)))"
```

A variável `dev` continua apontando para o mesmo PCF8574. **Esperado: `[239]`, equivalente a `0xEF` (`11101111` em binário)**: apenas P4 ficou baixo. O botão mudou `externalLevels`; o latch continua em `0xFF`.

**6. Soltar o botão e conferir a recuperação**

<!-- tutorial-model-monitor -->
```text
sysbus.button4 Release
emulation RunFor "0.001"
python "print(list(dev.Read(1)))"
```

`Release` solta o mesmo botão; o avanço seguinte entrega o nível alto ao PCF8574. **A leitura deve voltar a `[255]` (`0xFF`)**, sem uma nova escrita no latch. Isso confirma o caminho botão, conexão GPIO e estado observado pelo modelo.

### Verificar uma saída

Na **mesma sessão do Monitor**, escreva diretamente no modelo e depois aplique reset:

<!-- tutorial-output-monitor -->
```text
python "from System import Array, Byte; dev.Write(Array[Byte]([0xFE])); print(list(dev.Read(1)))"
sysbus.led0 State
python "dev.Reset(); print(list(dev.Read(1)))"
sysbus.led0 State
```

**Saída esperada:** `[254]` e `True` após a escrita; `[255]` e `False` após o reset. O bit P0 baixo acende o LED0.

Essas chamadas a `Read` e `Write` testam diretamente o modelo C#, não uma transação I2C. Digite `quit` antes de modificar a plataforma.

## 4. Conectar ao I2C do STM32

Crie `platforms/stm32.repl` para importar o STM32F4 distribuído com o Renode:

<!-- tutorial-file: platforms/stm32.repl -->
```text
using "platforms/cpus/stm32f4.repl"
```

Essa definição fornece a CPU e os periféricos internos do STM32, incluindo o controlador `i2c1`.

Em `platforms/pcf8574.repl`, troque **somente a primeira linha**, mantendo o `preinit` e todas as conexões de LEDs e botões:

<!-- tutorial-registration: platforms/pcf8574.repl -->
```text
pcf8574: Tutorial.PCF8574 @ i2c1 0x20
```

Agora `@ i2c1` registra o PCF8574 como dispositivo no **controlador I2C1 do STM32**. `0x20` é o endereço I2C de sete bits escolhido para o PCF8574, correspondente aos pinos A2, A1 e A0 em zero. Não é um endereço da memória do STM32. Referência: seção 7.3.3 do [datasheet](https://www.ti.com/lit/ds/symlink/pcf8574.pdf).

Atualize `scripts/platform.resc` para carregar o STM32 **antes** do PCF8574, pois `i2c1` precisa existir:

<!-- tutorial-update: scripts/platform.resc -->
```text
mach create "pcf8574-lab"
using sysbus
machine LoadPlatformDescription @platforms/stm32.repl
machine LoadPlatformDescription @platforms/pcf8574.repl
cpu PerformanceInMips 100
```

`PerformanceInMips` configura a taxa de execução simulada da CPU; não é o clock do SysTick.

### Verificar a conexão

**Terminal:**

```sh
renode --console --disable-gui --plain scripts/platform.resc
```

**Monitor:**

<!-- tutorial-i2c-monitor -->
```text
peripherals
python "dev = monitor.Machine['sysbus.i2c1.pcf8574']; print(list(dev.Read(1)))"
```

**Saída esperada:** a árvore de periféricos deve mostrar `pcf8574` sob `i2c1`, e a leitura deve retornar `[255]`. O caminho mudou de `sysbus.pcf8574` para `sysbus.i2c1.pcf8574`.

Isso verifica a montagem da plataforma. A CPU ainda não executou firmware; a comunicação I2C pelo mestre será testada na próxima seção. Digite `quit`.

## 5. Executar o firmware STM32

O firmware fornecido foi gerado a partir do STM32CubeMX para a placa **STM32F407G-DISC1**, MCU **STM32F407VGTx**, com HAL. O projeto completo está em [firmware](firmware), com o [arquivo CubeMX](firmware/pcf8574-demo.ioc) e o [main.c](firmware/Core/Src/main.c).

A aplicação inicializa o port em `0xFF`, mantém P4..P7 liberados para entrada e alterna um LED a cada 250 ms, percorrendo P0..P3. A sequência acende P0, P1, P2, P3 e depois apaga P0, P1, P2, P3. Mudanças das entradas são impressas na USART2.

Toda escrita usa `0xF0 | ledLevels`, preservando as entradas liberadas. O firmware não escreve de volta os bits lidos dos botões, o que poderia prender uma entrada em zero. A HAL recebe o endereço `0x20 << 1`; no REPL ele permanece `0x20`.

### Sobrescrever a configuração do SysTick

O firmware configura SYSCLK e HCLK em **168 MHz**, enquanto a plataforma `stm32f4.repl` do Renode 1.16.1 define `systickFrequency` em **72 MHz**. É necessário alinhar esse parâmetro para que os intervalos calculados pelo firmware tenham a duração esperada na simulação.

Substitua o conteúdo de `platforms/stm32.repl` por:

<!-- tutorial-update: platforms/stm32.repl -->
```text
using "platforms/cpus/stm32f4.repl"

nvic:
    systickFrequency: 168000000
```

O `using` importa a definição original; o bloco `nvic:` sobrescreve apenas o atributo indicado do dispositivo já declarado nessa definição. Os demais atributos são preservados, sem editar os arquivos da instalação do Renode.

Esse ajuste é aplicado durante a criação da plataforma. Encerre a sessão anterior e carregue uma nova após alterar o arquivo. Na seção 3 não havia CPU; na seção 4 ela ainda não executava firmware. Esses testes não dependiam dessa frequência.

### Carregar o binário

Crie `scripts/demo.resc`:

<!-- tutorial-file: scripts/demo.resc -->
```text
include @scripts/platform.resc
sysbus LoadELF @firmware/demo.elf
showAnalyzer sysbus.usart2
```

### Verificar o firmware

**Terminal:**

```sh
renode --console --disable-gui --plain scripts/demo.resc
```

Em um Monitor vazio, o equivalente é `include @scripts/demo.resc`. Atalho opcional: `python tools/lab.py --monitor`.

**Monitor**, em uma sessão nova e sem executar `start` antes:

<!-- tutorial-monitor -->
```text
emulation RunFor "0.270"
sysbus.led0 State
sysbus.button4 Press
emulation RunFor "0.100"
python "print(list(monitor.Machine['sysbus.i2c1.pcf8574'].Read(1)))"
sysbus.button4 Release
emulation RunFor "0.100"
python "print(list(monitor.Machine['sysbus.i2c1.pcf8574'].Read(1)))"
```

**Saída esperada:** LED0 em `True`; leituras `[238]` com P4 pressionado e `[254]` após soltar. Na UART, as mensagens de entrada devem passar por `INPUT P7..P4=0xF`, `0xE` e novamente `0xF`.

`RunFor` executa o intervalo de tempo virtual solicitado e para. Para execução contínua, use `start`; para interromper, `pause`. Saia com `quit`.

### Recompilar com STM32CubeIDE (opcional)

Importe a pasta `firmware` do repositório original em `File > Import > STM32CubeMX/STM32CubeIDE Project` e compile `pcf8574-demo` em `Debug`.

Na pasta `meu-pcf8574`, atualize o ELF:

```powershell
Copy-Item "$referencia/firmware/Debug/pcf8574-demo.elf" firmware/demo.elf
```

No Bash: `cp "$referencia/firmware/Debug/pcf8574-demo.elf" firmware/demo.elf`. Se reabriu o terminal, redefina `referencia` com o caminho do repositório original. Reinicie o Renode e repita a verificação anterior.

O ELF incluído foi compilado com Arm GCC 14.3 pelo auxiliar [build_firmware.py](tools/build_firmware.py). A importação gráfica no CubeIDE ainda não foi validada neste projeto.

## 6. Interagir pelo painel web

Encerre o Monitor e execute no **Terminal**:

```sh
python tools/lab.py
```

O painel abre em [localhost:8000](http://127.0.0.1:8000). Clique em um botão para pressionar e novamente para soltar. Os LEDs exibem os estados dos modelos do Renode; o terminal apresenta os caracteres enviados pelo firmware na USART2.

```text
Navegador --HTTP--> Python 3 --XML-RPC--> Renode
```

O painel é específico deste exemplo. Usa o [servidor remoto de testes do Renode](https://renode.readthedocs.io/en/latest/introduction/testing.html), compatível com Robot Framework, sem exigir sua instalação. `scripts/bridge.py` é executado pelo Python embutido do Renode para consultar os LEDs e capturar a UART; não deve ser executado diretamente com Python 3.

**Verificação sugerida:**

| Ação | Resultado esperado |
| --- | --- |
| Pressionar P4 | UART: `INPUT P7..P4=0xE` |
| Manter P4 e pressionar P7 | UART: `INPUT P7..P4=0x6` |
| Soltar ambos | UART termina em `INPUT P7..P4=0xF` |
| Pausar e avançar 250 ms | Um LED muda de estado por passo |

O efeito de um botão acionado enquanto pausado aparece no próximo avanço. Encerre com `Ctrl+C` no terminal.

## Testes automatizados (opcional)

Na pasta do projeto:

```sh
python tests/check.py all
```

São esperadas as linhas `PASS model:` e `PASS firmware:`. O primeiro teste verifica o modelo isolado; o segundo executa o firmware e verifica LEDs, botões e UART.

No repositório original, `python tests/check_tutorial.py` também reconstrói o exemplo a partir dos blocos deste README em uma pasta temporária e confere os comandos documentados.

## Limitações e diagnóstico

O modelo não implementa `INT`, correntes, curtos, temporização elétrica do I2C ou resolução de múltiplos drivers no mesmo pino. Trata-se de uma simulação funcional digital, não de equivalência elétrica completa ao CI.

| Problema | Ajuste |
| --- | --- |
| Renode não encontrado | Adicione a instalação ao PATH. No PowerShell, a alternativa é `& "C:/Program Files/Renode/renode.exe"`; nos auxiliares Python, use `--renode "caminho/do/executavel"` |
| Arquivo não encontrado | Execute na pasta `meu-pcf8574`; confira os nomes e extensões |
| Avisos de `flash_controller`, `rcc` ou `SYSCFG` no boot | Ocorrem nesta plataforma com a inicialização HAL; confira as leituras, o LED e a UART descritos na etapa 5 |
| Porta 8000 ocupada | Use `python tools/lab.py --port 8001` |
| Mudança no modelo ou ELF sem efeito | Encerre e abra uma nova sessão |
| Botão sem efeito | Avance o tempo virtual e confira se P4..P7 estão liberados |
| Tempo dos LEDs incorreto | Confira o SysTick de 168 MHz |

Validado no Windows com Renode 1.16.1. A execução no Linux e a importação gráfica no CubeIDE ainda requerem validação. Mantenha o painel local; não exponha as portas de controle do Renode à internet.
