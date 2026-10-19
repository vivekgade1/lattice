using FluentValidation;

namespace DnParity1.Dtos;

public class NoteInputValidator : AbstractValidator<NoteInput>
{
    public NoteInputValidator()
    {
        RuleFor(x => x.Title).NotEmpty().Length(1, 120);
        RuleFor(x => x.Priority).InclusiveBetween(1, 5);
    }
}
