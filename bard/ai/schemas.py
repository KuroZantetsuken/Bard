from pydantic import BaseModel, Field


class ThreadTitle(BaseModel):
    """
    A Pydantic model representing the structured response for a generated thread title.
    """

    title: str = Field(
        ...,
        description="A concise, descriptive, and appropriate title for a discussion thread, with a maximum of 100 characters.",
        max_length=100,
    )
