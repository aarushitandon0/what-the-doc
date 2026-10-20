# Opens a .docx in Microsoft Word, reports what Word itself resolves, and exits.
#
# Word's Paragraph object exposes *effective* formatting, so this is an independent
# check of the style resolver against the application the user actually cares about.
# Read-only, macros force-disabled, alerts off, and Word is always quit without saving.
#
# Refuses to run if Word is already open, because quitting a shared instance would
# close the user's own documents.
#
#   powershell -File word_check.ps1 -Path doc.docx -Paragraphs 1,4,6 -Pdf out.pdf

param(
    [Parameter(Mandatory = $true)][string]$Path,
    # Comma-separated 0-based ids. A string, because -File passes every argument as one.
    [string]$Paragraphs = '',
    [string]$Pdf = ''
)

$wanted = @()
if ($Paragraphs -ne '') {
    $wanted = @($Paragraphs.Split(',') | Where-Object { $_ -ne '' } | ForEach-Object { [int]$_.Trim() })
}

$ErrorActionPreference = 'Stop'

function Fail($reason) {
    Write-Output (ConvertTo-Json @{ available = $false; reason = $reason } -Compress)
    exit 0
}

if (Get-Process WINWORD -ErrorAction SilentlyContinue) {
    Fail 'Word is already running; refusing to automate a shared instance'
}
if ($null -eq [Type]::GetTypeFromProgID('Word.Application')) {
    Fail 'Word.Application COM class is not registered'
}

$full = (Resolve-Path -LiteralPath $Path).Path
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0              # wdAlertsNone
    $word.AutomationSecurity = 3         # msoAutomationSecurityForceDisable
    $document = $word.Documents.Open($full, $false, $true, $false)

    $result = @{ available = $true; path = $full }
    $result.pages = $document.ComputeStatistics(2)      # wdStatisticPages
    $result.words = $document.ComputeStatistics(0)      # wdStatisticWords
    $result.paragraph_count = $document.Paragraphs.Count
    # Reported as plain facts. Word does not expose "I repaired this file on open"
    # through the object model, so nothing here should be read as a repair signal.
    # compatibility_mode 15 means the file is treated as a current Word document;
    # a lower number means Word fell back to an older layout mode.
    $result.compatibility_mode = [int]$document.CompatibilityMode
    $result.marked_final = [bool]$document.Final

    $alignments = @{ 0 = 'left'; 1 = 'center'; 2 = 'right'; 3 = 'both' }
    $observed = @{}
    foreach ($index in $wanted) {
        if ($index -lt 0 -or $index -ge $document.Paragraphs.Count) {
            throw "Paragraph $index is outside the document's $($document.Paragraphs.Count) paragraphs"
        }
        $p = $document.Paragraphs.Item($index + 1)       # Word is 1-based
        $format = $p.Format
        $observed["$index"] = @{
            text              = $p.Range.Text.Trim()
            style             = $p.Style.NameLocal
            space_before_pt   = [double]$format.SpaceBefore
            space_after_pt    = [double]$format.SpaceAfter
            left_indent_pt    = [double]$format.LeftIndent
            alignment         = $alignments[[int]$format.Alignment]
            page_break_before = [bool]$format.PageBreakBefore
            keep_with_next    = [bool]$format.KeepWithNext
        }
    }
    $result.paragraphs = $observed

    if ($Pdf -ne '') {
        $target = [System.IO.Path]::GetFullPath($Pdf)
        $document.ExportAsFixedFormat($target, 17)       # wdExportFormatPDF
        $result.pdf = $target
    }
    Write-Output (ConvertTo-Json $result -Depth 6 -Compress)
}
catch {
    Write-Output (ConvertTo-Json @{ available = $false; reason = "$($_.Exception.Message)" } -Compress)
}
finally {
    # Close/Quit take ByRef parameters over COM, so they are called without arguments
    # after marking the document clean; nothing is ever saved.
    if ($null -ne $document) {
        try { $document.Saved = $true } catch {}
        try { $document.Close() } catch {}
    }
    if ($null -ne $word) { try { $word.Quit() } catch {} }
    foreach ($o in @($document, $word)) {
        if ($null -ne $o) { try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($o) } catch {} }
    }
}
