# What the independent reviewer checks

These are the ways a result with numbers goes wrong while every sentence in it still looks right. The reviewer reads
the finished work against them. The owner's own lessons (their feedback notes in Claude Code's memory) are added after
this list, and win where they are stricter.

### A true sentence with a false implication
"Costs fell 12%" is true and misleading if costs fell because half the items were dropped from the list. Ask what a
reader will conclude, then check that conclusion, not only the sentence.

### A ratio measured on a subset
When both sides lose the same rows, a difference of sums still holds; a ratio, a rate or a percentage does not. A
percentage quoted for "all" must be computed on all.

### An aggregate without a common unit
Adding pieces, boxes and kilograms gives a number that means nothing. A total needs one unit, or a conversion that is
stated.

### Rounding before ranking
Rounding first makes ties that are not there and changes who comes first. Rank on the full values; round only what is
shown.

### A baseline older than the code
A "before" number produced by an earlier version of the code measures the code change as well as the thing studied.
The baseline is re-run with today's code.

### A check that repeats its own filter
A test that selects rows with a condition and then asserts that condition can never fail. A check must be able to
fail: it compares against something the filter did not decide.

### Present is not used
"The field exists", "the file is there", "the option is on" do not show that the result used it. Check the effect.

### An aggregate that hides a split
A total that matches can hide two groups that are each wrong in opposite directions. Verify by group, not only in sum.

### Blank keys
Rows whose key is empty are silently dropped by a join, or matched to each other and multiplied. Count them before
and after every join and say what was done with them.

### A benefit measured on another list
A saving shown on the candidates is not the saving of what ships. Measure on the final list.

### Words that do not match the rows
A count is called what its filter was meant to return. Group the rows by where they go, their type and their date,
and check that the words describe what is there.

### "The same method" with a condition missing
Before saying a method was applied to a new case, list every condition the agreed one had (filters, inputs, date
windows, what was included and what was left out) and show each one carried.

### A number written before it was computed
Every figure in the text is tied to an output of this piece of work. "About" needs a computed value behind it.
