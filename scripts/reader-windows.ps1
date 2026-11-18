$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Speech

$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
$form = New-Object Windows.Forms.Form
try {
    $form.Text = 'Companion IA | Lector local'
    $form.ClientSize = [Drawing.Size]::new(650, 380)
    $form.MinimumSize = [Drawing.Size]::new(480, 260)

    $editor = New-Object Windows.Forms.TextBox
    $editor.Multiline = $true
    $editor.ScrollBars = 'Vertical'
    $editor.Dock = 'Fill'
    $editor.AccessibleName = 'Texto para leer'

    $controls = New-Object Windows.Forms.FlowLayoutPanel
    $controls.Dock = 'Bottom'
    $controls.Height = 42

    $voice = New-Object Windows.Forms.ComboBox
    $voice.DropDownStyle = 'DropDownList'
    $voice.AccessibleName = 'Voz'
    $voice.Width = 220
    $voices = @($speech.GetInstalledVoices() | Where-Object Enabled)
    foreach ($item in $voices) {
        [void]$voice.Items.Add($item.VoiceInfo.Name)
    }
    if ($voices.Count -gt 0) {
        $voice.SelectedIndex = 0
        for ($i = 0; $i -lt $voices.Count; $i++) {
            if ($voices[$i].VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'es') {
                $voice.SelectedIndex = $i
                break
            }
        }
    }

    $rate = New-Object Windows.Forms.NumericUpDown
    $rate.Minimum = -5
    $rate.Maximum = 5
    $rate.Width = 52
    $rate.AccessibleName = 'Velocidad'

    $read = New-Object Windows.Forms.Button
    $read.Text = 'Leer'
    $read.Enabled = $voices.Count -gt 0
    $stop = New-Object Windows.Forms.Button
    $stop.Text = 'Detener'
    $read.Add_Click({
        if ([string]::IsNullOrWhiteSpace($editor.Text)) { return }
        try {
            $speech.SpeakAsyncCancelAll()
            $speech.SelectVoice([string]$voice.SelectedItem)
            $speech.Rate = [int]$rate.Value
            [void]$speech.SpeakAsync($editor.Text)
        } catch {
            [void][Windows.Forms.MessageBox]::Show($form, $_.Exception.Message, 'Error de lectura')
        }
    })
    $stop.Add_Click({ $speech.SpeakAsyncCancelAll() })

    $controls.Controls.AddRange(@($voice, $rate, $read, $stop))
    $form.Controls.AddRange(@($editor, $controls))
    if ($voices.Count -eq 0) {
        [void][Windows.Forms.MessageBox]::Show('Instalá una voz de Windows para usar el lector.', 'Companion IA')
    }
    [void]$form.ShowDialog()
} finally {
    $speech.Dispose()
    $form.Dispose()
}